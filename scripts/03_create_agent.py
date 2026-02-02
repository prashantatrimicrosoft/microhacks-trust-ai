"""Create an AI Foundry Agent with Azure AI Search (SDK v1).

Creates the agent and saves its ID to the .env file for later use.
"""

import os
import json
from pathlib import Path
from dotenv import load_dotenv, set_key
from azure.identity import DefaultAzureCredential
from azure.ai.agents import AgentsClient
from azure.ai.agents.models import AzureAISearchTool, AzureAISearchQueryType

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


def get_agents_client():
    """Create AI Agents client using project endpoint."""
    endpoint = os.environ.get("AZURE_AI_PROJECT_ENDPOINT")
    if not endpoint:
        raise ValueError("AZURE_AI_PROJECT_ENDPOINT not set. Run 'azd up' first.")
    
    return AgentsClient(
        endpoint=endpoint,
        credential=DefaultAzureCredential(),
    )


def create_search_agent(agents_client: AgentsClient):
    """Create an agent with Azure AI Search tool."""
    model = os.environ.get("AZURE_CHAT_MODEL", "gpt-4o-mini")
    
    search_connection_id = os.environ.get("AZURE_AI_SEARCH_CONNECTION_ID", "search-connection")
    index_name = os.environ.get("AZURE_SEARCH_INDEX_NAME", "documents")
    
    ai_search = AzureAISearchTool(
        index_connection_id=search_connection_id,
        index_name=index_name,
        query_type=AzureAISearchQueryType.VECTOR_SEMANTIC_HYBRID,
        top_k=5,
    )
    
    agent = agents_client.create_agent(
        model=model,
        name="search-agent",
        instructions="""You are a helpful assistant that answers questions using the search tool.
When asked a question, use the search tool to find relevant information from the documents.
Always cite your sources and provide accurate information based on the search results.
If you cannot find relevant information, say so clearly.""",
        tools=ai_search.definitions,
        tool_resources=ai_search.resources,
    )
    
    return agent


def save_agent_id(agent_id: str):
    """Save agent ID to .env file."""
    if env_path.exists():
        set_key(str(env_path), "AZURE_AGENT_ID", agent_id)
        print(f"Saved AZURE_AGENT_ID={agent_id} to {env_path}")
    else:
        print(f"Warning: .env file not found at {env_path}")
        print(f"Agent ID: {agent_id}")


def main():
    print("Creating AI Search Agent (SDK v1)...")
    
    agents_client = get_agents_client()
    agent = create_search_agent(agents_client)
    
    print(f"\nAgent created successfully!")
    print(f"  ID: {agent.id}")
    print(f"  Name: {agent.name}")
    
    save_agent_id(agent.id)
    
    print("\nRun '04b_test_agent.py' to chat with this agent.")


if __name__ == "__main__":
    main()
