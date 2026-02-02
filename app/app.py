"""
Streamlit Chat Application with Azure OpenAI and RAG

A chat interface that uses Azure AI Search for retrieval and Azure OpenAI for generation.
Implements RAG (Retrieval Augmented Generation) pattern based on azure-search-openai-demo.

RAG Flow:
1. User Query → 
2. Azure AI Search (hybrid: vector + semantic) → 
3. Retrieved Documents → 
4. OpenAI (with context) → 
5. Response with citations

Designed to run on Azure App Service with system-assigned managed identity.
"""

import logging
import streamlit as st

# Import shared RAG service and tracing
from core import RAGService, get_settings
from core.tracing import setup_tracing

# Configure logging for debugging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize tracing on module load
settings = get_settings()
tracing_enabled = setup_tracing(
    service_name="rag-webapp",
    connection_string=settings.applicationinsights_connection_string
)
if tracing_enabled:
    logger.info("✅ Tracing initialized - sending telemetry to Azure Application Insights")
else:
    logger.warning("⚠️ Tracing not enabled - set APPLICATIONINSIGHTS_CONNECTION_STRING to enable")

# Page configuration
st.set_page_config(
    page_title="Azure OpenAI Chat with RAG",
    page_icon="💬",
    layout="centered"
)

# App title
st.title("💬 Azure OpenAI Chat with RAG")
st.markdown("*Powered by Azure AI Search and Azure OpenAI*")
st.markdown("---")


@st.cache_resource
def get_rag_service() -> RAGService:
    """
    Create cached RAG service instance.
    
    Uses st.cache_resource to ensure only one instance is created.
    """
    settings = get_settings()
    return RAGService(settings)


def format_citations_for_display(documents: list) -> str:
    """
    Format retrieved documents for display in the sidebar.
    """
    if not documents:
        return "No documents retrieved."
    
    citations = []
    for i, doc in enumerate(documents, 1):
        source_info = f"**{i}. {doc.title or 'Untitled'}**"
        if doc.source:
            source_info += f"\n   📄 {doc.source}"
        if doc.page_number:
            source_info += f" (Page {doc.page_number})"
        if doc.reranker_score:
            source_info += f"\n   🎯 Relevance: {doc.reranker_score:.2f}"
        citations.append(source_info)
    
    return "\n\n".join(citations)


def main():
    # Get settings for display
    settings = get_settings()
    
    # Initialize session state for chat history and retrieved documents
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "last_documents" not in st.session_state:
        st.session_state.last_documents = []
    
    # Initialize RAG service
    try:
        rag_service = get_rag_service()
    except Exception as e:
        st.error(f"Failed to initialize RAG service: {e}")
        st.info("Make sure the app is configured with proper Azure credentials.")
        return
    
    # Display chat history
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
    
    # Chat input
    if prompt := st.chat_input("Ask a question about your documents..."):
        # Add user message to chat history
        st.session_state.messages.append({"role": "user", "content": prompt})
        
        # Display user message
        with st.chat_message("user"):
            st.markdown(prompt)
        
        # Get and display assistant response
        with st.chat_message("assistant"):
            message_placeholder = st.empty()
            full_response = ""
            
            try:
                # Build conversation history (excluding current message)
                conversation_history = [
                    {"role": msg["role"], "content": msg["content"]}
                    for msg in st.session_state.messages[:-1]
                ]
                
                # Use RAG service for streaming response
                with st.spinner("🔍 Searching documents..."):
                    # Get initial documents for display
                    documents = rag_service.get_documents_for_query(prompt)
                    st.session_state.last_documents = documents
                    st.session_state.debug_search_count = len(documents)
                
                # Stream the response
                final_metadata = None
                for chunk, metadata in rag_service.chat_stream(
                    query=prompt,
                    conversation_history=conversation_history
                ):
                    if chunk:
                        full_response += chunk
                        message_placeholder.markdown(full_response + "▌")
                    if metadata:
                        final_metadata = metadata
                        # Store debug info
                        st.session_state.debug_sources = metadata.sources_text
                        st.session_state.debug_system_prompt = metadata.system_prompt
                
                message_placeholder.markdown(full_response)
                
            except Exception as e:
                full_response = f"Error: {str(e)}"
                message_placeholder.error(full_response)
                logger.error(f"Chat error: {e}", exc_info=True)
        
        # Add assistant response to chat history
        st.session_state.messages.append({"role": "assistant", "content": full_response})
    
    # Sidebar with info and controls
    with st.sidebar:
        st.header("ℹ️ About")
        st.markdown("""
        This is a **RAG-powered** chat application using:
        - 🔍 **Azure AI Search** for document retrieval
        - 🤖 **Azure OpenAI** for response generation
        
        **RAG Flow:**
        1. User Query
        2. Search Index (hybrid + semantic)
        3. Retrieved Documents
        4. OpenAI with Context
        5. Response with Citations
        """)
        
        st.markdown("---")
        
        # Clear chat button
        if st.button("🗑️ Clear Chat", use_container_width=True):
            st.session_state.messages = []
            st.session_state.last_documents = []
            st.rerun()
        
        st.markdown("---")
        
        # Configuration info
        st.subheader("⚙️ Configuration")
        st.text_input("OpenAI Endpoint", value=settings.azure_openai_endpoint or "Not configured", disabled=True)
        st.text_input("Search Endpoint", value=settings.azure_ai_search_endpoint or "Not configured", disabled=True)
        st.text_input("Model", value=settings.azure_openai_chat_deployment, disabled=True)
        st.text_input("Index", value=settings.azure_search_index_name, disabled=True)
        
        # Show retrieved sources/citations
        if st.session_state.get("last_documents"):
            st.markdown("---")
            st.subheader("📚 Retrieved Sources")
            with st.expander("View sources", expanded=True):
                st.markdown(format_citations_for_display(st.session_state.last_documents))
        
        # Debug section
        st.markdown("---")
        st.subheader("🐛 Debug Info")
        
        # Show search results count
        search_count = st.session_state.get("debug_search_count", 0)
        st.metric("Documents Retrieved", search_count)
        
        # Show if sources were passed to LLM
        if st.session_state.get("debug_sources"):
            sources_len = len(st.session_state.debug_sources)
            st.metric("Sources Text Length", f"{sources_len} chars")
            
            with st.expander("View raw sources sent to LLM", expanded=False):
                st.code(st.session_state.debug_sources[:2000] + "..." if sources_len > 2000 else st.session_state.debug_sources)
        
        # Show system prompt
        if st.session_state.get("debug_system_prompt"):
            with st.expander("View full system prompt", expanded=False):
                st.code(st.session_state.debug_system_prompt[:3000] + "..." if len(st.session_state.debug_system_prompt) > 3000 else st.session_state.debug_system_prompt)


if __name__ == "__main__":
    main()
