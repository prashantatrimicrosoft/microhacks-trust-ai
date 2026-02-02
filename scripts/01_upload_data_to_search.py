"""Upload PDF files to Azure AI Search with page-aware chunking and sentence boundaries."""

import os
import json
import re
from pathlib import Path
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from openai import AzureOpenAI
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex,
    SearchField,
    SearchFieldDataType,
    VectorSearch,
    HnswAlgorithmConfiguration,
    VectorSearchProfile,
    AzureOpenAIVectorizer,
    AzureOpenAIVectorizerParameters,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
)
from pypdf import PdfReader

# Load environment from azd
azure_dir = Path(__file__).parent.parent / ".azure"
env_name = os.environ.get("AZURE_ENV_NAME", "")
if not env_name and (azure_dir / "config.json").exists():
    with open(azure_dir / "config.json") as f:
        config = json.load(f)
        env_name = config.get("defaultEnvironment", "")

env_path = azure_dir / env_name / ".env"
if env_path.exists():
    load_dotenv(env_path)

INDEX_NAME = "documents"
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200


def get_openai_client():
    """Create Azure OpenAI client using AI endpoint."""
    endpoint = os.environ.get("AZURE_AI_ENDPOINT")
    if not endpoint:
        raise ValueError("AZURE_AI_ENDPOINT not set. Run 'azd up' first.")
    
    credential = DefaultAzureCredential()
    token = credential.get_token("https://cognitiveservices.azure.com/.default")
    
    return AzureOpenAI(
        azure_endpoint=endpoint,
        api_key=token.token,
        api_version="2024-10-21",
    )


def get_search_clients():
    """Create Azure Search clients."""
    endpoint = os.environ.get("AZURE_AI_SEARCH_ENDPOINT")
    if not endpoint:
        raise ValueError("AZURE_AI_SEARCH_ENDPOINT not set")
    
    credential = DefaultAzureCredential()
    index_client = SearchIndexClient(endpoint, credential)
    search_client = SearchClient(endpoint, INDEX_NAME, credential)
    
    return index_client, search_client


def create_index(index_client: SearchIndexClient):
    """Create or update the search index with integrated vectorizer."""
    embedding_model = os.environ.get("AZURE_EMBEDDING_MODEL", "text-embedding-3-large")
    ai_endpoint = os.environ.get("AZURE_AI_ENDPOINT")
    
    fields = [
        SearchField(name="id", type=SearchFieldDataType.String, key=True),
        SearchField(name="content", type=SearchFieldDataType.String, searchable=True),
        SearchField(name="title", type=SearchFieldDataType.String, searchable=True, filterable=True),
        SearchField(name="source", type=SearchFieldDataType.String, filterable=True),
        SearchField(name="page_number", type=SearchFieldDataType.Int32, filterable=True, sortable=True),
        SearchField(name="chunk_id", type=SearchFieldDataType.Int32, sortable=True),
        SearchField(
            name="embedding",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=3072,  # text-embedding-3-large uses 3072 dimensions
            vector_search_profile_name="default-profile"
        ),
    ]
    
    # Integrated vectorizer for query-time embedding
    vectorizer = AzureOpenAIVectorizer(
        vectorizer_name="openai-vectorizer",
        parameters=AzureOpenAIVectorizerParameters(
            resource_url=ai_endpoint,
            deployment_name=embedding_model,
            model_name=embedding_model,
        )
    )
    
    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="default-algorithm")],
        profiles=[VectorSearchProfile(
            name="default-profile",
            algorithm_configuration_name="default-algorithm",
            vectorizer_name="openai-vectorizer"
        )],
        vectorizers=[vectorizer]
    )
    
    # Semantic configuration for hybrid search
    semantic_config = SemanticConfiguration(
        name="default-semantic",
        prioritized_fields=SemanticPrioritizedFields(
            content_fields=[SemanticField(field_name="content")],
            title_field=SemanticField(field_name="title"),
        )
    )
    semantic_search = SemanticSearch(configurations=[semantic_config])
    
    index = SearchIndex(
        name=INDEX_NAME,
        fields=fields,
        vector_search=vector_search,
        semantic_search=semantic_search
    )
    index_client.create_or_update_index(index)
    print(f"Index '{INDEX_NAME}' ready with integrated vectorizer")


def extract_pages_from_pdf(filepath: Path) -> list[tuple[int, str]]:
    """Extract text content from each page of a PDF file.
    
    Returns list of (page_number, text) tuples (1-indexed page numbers).
    """
    reader = PdfReader(filepath)
    pages = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text()
        if text and text.strip():
            pages.append((i + 1, text.strip()))
    return pages


def split_into_sentences(text: str) -> list[str]:
    """Split text into sentences, preserving sentence boundaries."""
    # Split on sentence-ending punctuation followed by space or newline
    # This regex handles ., !, ? followed by whitespace
    sentences = re.split(r'(?<=[.!?])\s+', text)
    return [s.strip() for s in sentences if s.strip()]


def chunk_text_by_sentences(text: str, max_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into chunks that respect sentence boundaries.
    
    Chunks will not exceed max_size and will not cut mid-sentence.
    Overlap is applied by including trailing sentences from previous chunk.
    """
    sentences = split_into_sentences(text)
    
    if not sentences:
        return [text] if text.strip() else []
    
    chunks = []
    current_chunk = []
    current_length = 0
    overlap_sentences = []
    
    for sentence in sentences:
        sentence_len = len(sentence)
        
        # If single sentence exceeds max_size, include it anyway (don't break mid-sentence)
        if sentence_len > max_size:
            # Save current chunk if it has content
            if current_chunk:
                chunks.append(' '.join(current_chunk))
                # Keep last few sentences for overlap
                overlap_sentences = current_chunk[-2:] if len(current_chunk) >= 2 else current_chunk[:]
            
            chunks.append(sentence)
            current_chunk = []
            current_length = 0
            overlap_sentences = []
            continue
        
        # Check if adding this sentence would exceed max_size
        potential_length = current_length + sentence_len + (1 if current_chunk else 0)
        
        if potential_length > max_size and current_chunk:
            # Save current chunk
            chunks.append(' '.join(current_chunk))
            
            # Start new chunk with overlap from previous
            overlap_text_len = sum(len(s) for s in overlap_sentences) + len(overlap_sentences)
            if overlap_text_len < overlap and overlap_sentences:
                current_chunk = overlap_sentences[:]
                current_length = overlap_text_len
            else:
                current_chunk = []
                current_length = 0
        
        # Add sentence to current chunk
        current_chunk.append(sentence)
        current_length += sentence_len + (1 if len(current_chunk) > 1 else 0)
        
        # Track sentences for potential overlap
        overlap_sentences = current_chunk[-2:] if len(current_chunk) >= 2 else current_chunk[:]
    
    # Don't forget the last chunk
    if current_chunk:
        chunks.append(' '.join(current_chunk))
    
    return chunks


def get_embedding(client: AzureOpenAI, text: str) -> list[float]:
    """Generate embedding for text using OpenAI client."""
    model = os.environ.get("AZURE_EMBEDDING_MODEL", "text-embedding-3-large")
    response = client.embeddings.create(input=[text], model=model)
    return response.data[0].embedding


def main():
    data_dir = Path(__file__).parent.parent / "data"
    if not data_dir.exists():
        print("No data folder found. Run generate_sample_data.py first.")
        return
    
    pdf_files = list(data_dir.glob("*.pdf"))
    if not pdf_files:
        print("No PDF files found in data folder.")
        return
    
    print(f"Found {len(pdf_files)} PDF file(s)")
    
    openai_client = get_openai_client()
    index_client, search_client = get_search_clients()
    
    # Create index
    create_index(index_client)
    
    # Process each PDF
    documents = []
    for pdf_path in pdf_files:
        print(f"Processing: {pdf_path.name}")
        
        pages = extract_pages_from_pdf(pdf_path)
        
        for page_num, page_text in pages:
            chunks = chunk_text_by_sentences(page_text)
            
            for chunk_idx, chunk in enumerate(chunks):
                # ID format: filename_pagenumber_chunknumber (using underscores - # not allowed in keys)
                doc_id = f"{pdf_path.stem}_p{page_num}_c{chunk_idx}"
                
                embedding = get_embedding(openai_client, chunk)
                
                doc = {
                    "id": doc_id,
                    "content": chunk,
                    "title": pdf_path.stem.replace("_", " ").title(),
                    "source": pdf_path.name,
                    "page_number": page_num,
                    "chunk_id": chunk_idx,
                    "embedding": embedding
                }
                documents.append(doc)
    
    # Upload to search
    print(f"\nUploading {len(documents)} chunks to search index...")
    result = search_client.upload_documents(documents)
    succeeded = sum(1 for r in result if r.succeeded)
    print(f"Uploaded {succeeded}/{len(documents)} documents")
    
    print("\nDone!")


if __name__ == "__main__":
    main()
