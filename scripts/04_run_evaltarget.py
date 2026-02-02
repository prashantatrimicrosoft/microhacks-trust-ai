"""
Evaluate Target Application (Container App Backend API)
Uses Azure AI Evaluation SDK to evaluate the deployed RAG application
Results are uploaded to Azure AI Foundry portal for analysis
"""

import os
import json
import contextlib
import multiprocessing
import requests
from pathlib import Path
from typing import TypedDict
from pprint import pprint

import pandas as pd
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.ai.evaluation import evaluate
from azure.ai.evaluation import RelevanceEvaluator, GroundednessEvaluator
from azure.ai.evaluation import AzureOpenAIModelConfiguration

# ----------------------------------------------
# Load environment from azd
# ----------------------------------------------
azure_dir = Path(__file__).parent.parent / ".azure"
env_name = os.environ.get("AZURE_ENV_NAME", "")
if not env_name and (azure_dir / "config.json").exists():
    with open(azure_dir / "config.json") as f:
        config = json.load(f)
        env_name = config.get("defaultEnvironment", "")

env_path = azure_dir / env_name / ".env"
if env_path.exists():
    load_dotenv(env_path)


# ----------------------------------------------
# Define Response Type
# ----------------------------------------------
class TargetResponse(TypedDict):
    """Response structure from target application evaluation."""
    response: str
    context: str


# ----------------------------------------------
# Target Function to Query Container App API
# ----------------------------------------------
def evaluate_target_application(question: str) -> TargetResponse:
    """
    Target function that calls the Container App backend API.
    This function will be evaluated by the Azure AI evaluation SDK.
    
    Args:
        question: The user question to ask the application
        
    Returns:
        TargetResponse with response and context for evaluation
    """
    # Read BACKEND_URL from environment each time to support multiprocessing
    backend_url = os.getenv("AZURE_CONTAINER_APP_URL", "")
    
    if not backend_url:
        return TargetResponse(
            response="Error: AZURE_CONTAINER_APP_URL not set",
            context=""
        )
    
    try:
        # Call the /chat endpoint of the Container App
        response = requests.post(
            f"{backend_url}/chat",
            json={
                "message": question,
                "conversation_history": [],
                "system_prompt": "You are a helpful AI assistant. Provide clear, accurate, and helpful responses.",
                "max_tokens": 2048
            },
            headers={"Content-Type": "application/json"},
            timeout=120
        )
        response.raise_for_status()
        
        result = response.json()
        
        # Extract the answer from the response
        # Adjust these fields based on your API response structure
        answer = result.get("response", result.get("message", ""))
        
        # If your API returns context/sources, extract them here
        # Otherwise, use empty string for groundedness evaluation
        context = result.get("context", result.get("sources", ""))
        if isinstance(context, list):
            context = "\n\n".join(str(c) for c in context)
        
        return TargetResponse(
            response=answer,
            context=context if context else ""
        )
        
    except requests.exceptions.Timeout:
        print(f"Timeout calling target application for question: {question[:50]}...")
        return TargetResponse(
            response="Error: Request timed out",
            context=""
        )
    except requests.exceptions.RequestException as e:
        print(f"Error calling target application: {e}")
        return TargetResponse(
            response=f"Error: {str(e)}",
            context=""
        )


# ----------------------------------------------
# Main Evaluation Runner
# ----------------------------------------------
if __name__ == "__main__":
    # Workaround for multiprocessing issue on linux
    with contextlib.suppress(RuntimeError):
        multiprocessing.set_start_method("spawn", force=True)
    
    # Get backend URL
    backend_url = os.environ.get("AZURE_CONTAINER_APP_URL", "")
    if not backend_url:
        print("Error: AZURE_CONTAINER_APP_URL not set.")
        print("Set it in your .env file or run 'azd up' to provision infrastructure.")
        exit(1)
    
    # Configure Azure AI project for uploading results
    azure_ai_project = os.environ.get("AZURE_AI_PROJECT_ENDPOINT")
    if not azure_ai_project:
        print("Error: AZURE_AI_PROJECT_ENDPOINT not set")
        exit(1)
    
    # Configure evaluator model
    model_config = AzureOpenAIModelConfiguration(
        azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT") or os.environ.get("AZURE_AI_ENDPOINT"),
        azure_deployment=os.environ.get("AZURE_CHAT_MODEL", "gpt-4o-mini"),
        api_version="2024-08-01-preview",
    )
    
    # Initialize evaluators
    relevance_eval = RelevanceEvaluator(model_config)
    groundedness_eval = GroundednessEvaluator(model_config)
    
    # Path to evaluation data
    data_path = Path(__file__).parent.parent / "evals" / "ground_truth_small.jsonl"
    #data_path = Path(__file__).parent.parent / "evals" / "ground_truth.jsonl"
    output_dir = Path(__file__).parent.parent / "evals" / "results" / "quality"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "results_target.jsonl"
    
    if not data_path.exists():
        # Try the full ground truth file
        data_path = Path(__file__).parent.parent / "evals" / "ground_truth.jsonl"
        if not data_path.exists():
            print(f"Error: Data file not found.")
            print("Expected: evals/ground_truth_small.jsonl or evals/ground_truth.jsonl")
            exit(1)
    
    # Check if backend is accessible
    print(f"\nChecking backend connectivity...")
    try:
        # Try health check endpoint first
        health_url = f"{backend_url}/api/health"
        health_check = requests.get(health_url, timeout=10)
        print(f"✅ Backend is accessible at {backend_url}")
    except requests.exceptions.RequestException:
        try:
            # Try root endpoint as fallback
            root_check = requests.get(backend_url, timeout=10)
            print(f"✅ Backend is accessible at {backend_url}")
        except requests.exceptions.RequestException as e:
            print(f"⚠️  Warning: Cannot connect to backend at {backend_url}")
            print(f"   Error: {e}")
            print(f"\n   Make sure the Container App is running.")
            print(f"   If the app is idle, it may take a moment to start.")
            response = input("\nContinue anyway? (y/n): ")
            if response.lower() != 'y':
                exit(1)
    
    print(f"\n🔍 Starting evaluation of target application...")
    print(f"   Backend URL: {backend_url}")
    print(f"   Data file: {data_path}")
    print(f"   Output file: {output_path}")
    print(f"   Evaluators: relevance, groundedness")
    print("")
    
    # Run evaluation with the target function
    result = evaluate(
        data=str(data_path),
        target=evaluate_target_application,
        evaluation_name="evaluate_container_app_target",
        evaluators={
            "relevance": relevance_eval,
            "groundedness": groundedness_eval,
        },
        evaluator_config={
            "relevance": {
                "column_mapping": {
                    "query": "${data.question}",
                    "response": "${target.response}"
                }
            },
            "groundedness": {
                "column_mapping": {
                    "query": "${data.question}",
                    "response": "${target.response}",
                    "context": "${data.truth}"  # Use ground truth as context for evaluation
                }
            }
        },
        azure_ai_project=azure_ai_project,
        output_path=str(output_path),
    )
    
    # Display results
    tabular_result = pd.DataFrame(result.get("rows"))
    
    print("\n" + "=" * 60)
    print("--- Summarized Metrics ---")
    pprint(result["metrics"])
    print("\n--- Tabular Result ---")
    pprint(tabular_result)
    print("\n--- Evaluation Complete ---")
    print(f"Results saved to: {output_path}")
    
    if "studio_url" in result:
        print(f"\n🔗 View evaluation results in Microsoft Foundry:")
        print(f"   {result['studio_url']}")
    
    print("=" * 60)


# ----------------------------------------------
# How to Run This Evaluation:
# 
# 1. Make sure the Container App is deployed and running:
#    - Run 'azd up' to provision infrastructure
#    - Run './scripts/06_deploy_container_apps.sh' to deploy the app
#    - Verify AZURE_CONTAINER_APP_URL is set in your .env file
#
# 2. Ensure you have evaluation data file:
#    - Default: evals/ground_truth_small.jsonl (for quick tests)
#    - Full: evals/ground_truth.jsonl (for complete evaluation)
#    - Format: Each line should have "question" and "truth" fields
#    - Example: {"question": "What is Azure?", "truth": "Azure is..."}
#
# 3. Run the evaluation:
#    cd scripts
#    python 03_run_evaltarget.py
#
# Expected Output:
#   - Calls your Container App for each question in the data file
#   - Evaluates relevance of responses to questions
#   - Evaluates groundedness of responses against ground truth
#   - Saves results locally to evals/results_target.jsonl
#   - Uploads results to Azure AI Foundry portal
#   - Displays summarized metrics and tabular results
# ----------------------------------------------
