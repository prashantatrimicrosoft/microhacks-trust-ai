"""
FastAPI REST API for Azure OpenAI Chat with RAG

Provides REST endpoints for programmatic access to Azure OpenAI chat functionality
with RAG (Retrieval Augmented Generation) support.
Uses managed identity authentication when deployed to Azure App Service.
"""

from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

# Import shared RAG service and tracing
from core import RAGService, RAGResponse, get_settings
from core.tracing import setup_tracing, add_span_attribute, add_span_event


# Global RAG service instance
rag_service: Optional[RAGService] = None


def get_rag_service() -> RAGService:
    """
    Get or create RAG service instance.
    """
    global rag_service
    
    if rag_service is None:
        settings = get_settings()
        if not settings.azure_openai_endpoint:
            raise ValueError("AZURE_OPENAI_ENDPOINT environment variable not set")
        rag_service = RAGService(settings)
    
    return rag_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize resources on startup."""
    # Initialize tracing first
    settings = get_settings()
    tracing_enabled = setup_tracing(
        service_name="rag-api",
        connection_string=settings.applicationinsights_connection_string
    )
    if tracing_enabled:
        print("✅ Tracing initialized - sending telemetry to Azure Application Insights")
    else:
        print("⚠️ Tracing not enabled - set APPLICATIONINSIGHTS_CONNECTION_STRING to enable")
    
    try:
        get_rag_service()
        print("RAG service initialized successfully")
    except Exception as e:
        print(f"Warning: Failed to initialize RAG service: {e}")
    yield
    # Cleanup on shutdown
    global rag_service
    rag_service = None


# FastAPI app
app = FastAPI(
    title="Azure OpenAI Chat API with RAG",
    description="REST API for RAG-powered chat interactions with Azure OpenAI",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware for cross-origin requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Request/Response Models
class Message(BaseModel):
    """A chat message."""
    role: str = Field(..., description="Message role: 'user', 'assistant', or 'system'")
    content: str = Field(..., description="Message content")


class ChatRequest(BaseModel):
    """Chat request payload."""
    message: str = Field(..., description="The user's message")
    conversation_history: list[Message] = Field(
        default=[],
        description="Previous messages in the conversation for context"
    )
    system_prompt: str = Field(
        default="",
        description="Custom system prompt (leave empty for RAG default)"
    )
    max_tokens: int = Field(default=2048, ge=1, le=4096, description="Maximum tokens in response")
    temperature: float = Field(default=0.7, ge=0, le=2, description="Sampling temperature")
    use_rag: bool = Field(default=True, description="Whether to use RAG (document retrieval)")
    top_k: int = Field(default=5, ge=1, le=20, description="Number of documents to retrieve for RAG")


class ChatResponse(BaseModel):
    """Chat response payload."""
    response: str = Field(..., description="The assistant's response")
    model: str = Field(..., description="Model used for completion")
    usage: dict = Field(default={}, description="Token usage statistics")
    sources: list[dict] = Field(default=[], description="Retrieved documents (when using RAG)")


class DocumentResponse(BaseModel):
    """Document search response."""
    documents: list[dict] = Field(..., description="Retrieved documents")
    query: str = Field(..., description="The search query")


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    endpoint_configured: bool
    model: str


# API Endpoints
@app.get("/", tags=["Root"])
async def root():
    """Root endpoint with API information."""
    return {
        "name": "Azure OpenAI Chat API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health"
    }


@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check():
    """
    Health check endpoint.
    
    Returns the service status and configuration state.
    """
    settings = get_settings()
    
    return HealthResponse(
        status="healthy",
        endpoint_configured=bool(settings.azure_openai_endpoint),
        model=settings.azure_openai_chat_deployment
    )


@app.post("/chat", response_model=ChatResponse, tags=["Chat"])
async def chat(request: ChatRequest):
    """
    Send a message and receive a complete response.
    
    Uses RAG by default to retrieve relevant documents before generating a response.
    Set use_rag=false for direct chat without document retrieval.
    """
    try:
        service = get_rag_service()
        settings = get_settings()
        
        # Convert conversation history to list of dicts
        history = [{"role": msg.role, "content": msg.content} for msg in request.conversation_history]
        
        if request.use_rag:
            # Use RAG flow
            rag_response = service.chat(
                query=request.message,
                conversation_history=history,
                top_k=request.top_k
            )
            
            # Convert documents to dicts for response
            sources = [
                {
                    "title": doc.title,
                    "source": doc.source,
                    "page_number": doc.page_number,
                    "score": doc.score
                }
                for doc in rag_response.documents
            ]
            
            return ChatResponse(
                response=rag_response.answer,
                model=settings.azure_openai_chat_deployment,
                usage={},
                sources=sources
            )
        else:
            # Direct chat without RAG
            messages = [{"role": "system", "content": request.system_prompt or "You are a helpful AI assistant."}]
            messages.extend(history)
            messages.append({"role": "user", "content": request.message})
            
            response = service.generate_response(
                messages=messages,
                stream=False,
                max_tokens=request.max_tokens,
                temperature=request.temperature
            )
            
            return ChatResponse(
                response=response.choices[0].message.content,
                model=response.model,
                usage={
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens
                },
                sources=[]
            )
        
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chat completion failed: {str(e)}")


@app.post("/chat/stream", tags=["Chat"])
async def chat_stream(request: ChatRequest):
    """
    Send a message and receive a streaming response.
    
    Returns a Server-Sent Events (SSE) stream with response chunks.
    Uses RAG by default to retrieve relevant documents before generating.
    """
    try:
        service = get_rag_service()
        
        # Convert conversation history to list of dicts
        history = [{"role": msg.role, "content": msg.content} for msg in request.conversation_history]
        
        async def generate():
            if request.use_rag:
                # Use RAG streaming
                for chunk, metadata in service.chat_stream(
                    query=request.message,
                    conversation_history=history,
                    top_k=request.top_k
                ):
                    if chunk:
                        yield f"data: {chunk}\n\n"
            else:
                # Direct streaming without RAG
                messages = [{"role": "system", "content": request.system_prompt or "You are a helpful AI assistant."}]
                messages.extend(history)
                messages.append({"role": "user", "content": request.message})
                
                response = service.generate_response(
                    messages=messages,
                    stream=True,
                    max_tokens=request.max_tokens,
                    temperature=request.temperature
                )
                
                for chunk in response:
                    if chunk.choices and chunk.choices[0].delta.content:
                        content = chunk.choices[0].delta.content
                        yield f"data: {content}\n\n"
            
            yield "data: [DONE]\n\n"
        
        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            }
        )
        
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chat completion failed: {str(e)}")


@app.get("/search", response_model=DocumentResponse, tags=["Search"])
async def search_documents(query: str, top_k: int = 5):
    """
    Search for relevant documents without generating a response.
    
    Useful for document discovery and testing retrieval.
    """
    try:
        service = get_rag_service()
        
        documents = service.get_documents_for_query(query, top_k=top_k)
        
        return DocumentResponse(
            documents=[
                {
                    "title": doc.title,
                    "source": doc.source,
                    "page_number": doc.page_number,
                    "content": doc.content[:500] + "..." if len(doc.content) > 500 else doc.content,
                    "score": doc.score,
                    "reranker_score": doc.reranker_score
                }
                for doc in documents
            ],
            query=query
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
