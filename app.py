import os
import asyncio
import warnings
from flask import Flask, request, jsonify, render_template
from dotenv import load_dotenv
from google.cloud import firestore
import random
from datetime import datetime

# Suppress the ADK experimental feature warning globally
warnings.filterwarnings("ignore", category=UserWarning)

# ADK and Toolbox Imports
from google import adk
from toolbox_core import ToolboxSyncClient
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

load_dotenv()
PROJECT_ID = os.getenv("PROJECT_ID", "*****")
GOOGLE_CLOUD_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
APP_NAME = "FitnessAgent"
USER = "default_user"
MODEL = os.getenv("MODEL", "gemini-2.5-flash")
TOOLBOX_URL = os.getenv("MCP_TOOLBOX_FITNESS_URL", "")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

db = firestore.Client(project=PROJECT_ID)

app = Flask(__name__)

all_tools = []
try:
    toolboxCore = ToolboxSyncClient(TOOLBOX_URL)
    
    # Strictly load the toolset mapped in your tools.yaml
    all_tools = toolboxCore.load_toolset("alloydb_tools")
    
    print(f"-> MCP Client: Successfully connected to {TOOLBOX_URL}")
    print(f"-> MCP Tools Loaded: {len(all_tools)} tools found.")
    for t in all_tools:
        # Better extraction to reveal the actual function name hidden in the MCP wrapper
        tool_name = getattr(t, 'name', None) or getattr(t, '__name__', None) or str(t)
        print(f"   - Tool detected: {tool_name}")
except Exception as e:
    print(f"FATAL ERROR: Could not connect to MCP Toolbox Server at {TOOLBOX_URL}. Error: {e}")

store_manager_agent = adk.Agent(
    name="FitnessManager",
    model=MODEL,
    description="Fitness manager recommending fitness routines.",
    tools=all_tools,
    instruction="""
    You are the fitness manager. You have access to a BigQuery ML fitness model via your external tool named 'recommend_workout'.
    
    CRITICAL OPERATING PROTOCOLS:
    1. You MUST IMMEDIATELY invoke the `recommend_workout` tool. 
    2. DO NOT make up or guess a workout routine. DO NOT apologize. Just call the tool.
    3. The user will provide parameters in their message. Extract them and pass them exactly to the tool using THESE EXACT argument names:
       - usermood (String)
       - physicalfocus (String)
       - fitnesslevel (String)
    4. Once the tool returns the optimal_routine, format it beautifully using markdown bullet points and bold headings.
    5. Include a medical disclaimer that this routine should not be considered an alternative to consulting a medical professional.
    """
)

session_service = InMemorySessionService()

runner = adk.Runner(
    agent=store_manager_agent,
    app_name=APP_NAME,
    session_service=session_service,
)

global_session = None

async def initialize_session():
    global global_session
    try:
        global_session = await session_service.create_session(app_name=APP_NAME, user_id=USER)
        print(f"-> Session initialized successfully: {global_session.id}")
    except Exception as e:
        print(f"Error creating session: {e}")

asyncio.run(initialize_session())

@app.route('/')
def index():
    return render_template('mcpfitness.html')

@app.route('/dashboard')
def dashboard():
    return render_template(
        'livemap.html',
        GOOGLE_MAPS_API_KEY=os.getenv("GOOGLE_MAPS_API_KEY", ""),
        FIREBASE_CONFIG=os.getenv("FIREBASE_CONFIG", "{}")
    )

@app.route('/chat', methods=['POST'])
def chat():
    global global_session
    user_input = request.json.get('message', '')
    user_name = request.json.get('user_name', 'ANO') # Capture the 3-letter name
    
    # Extract dynamic geolocation parameters from the frontend
    req_lat = request.json.get('lat')
    req_lng = request.json.get('lng')
    
    if not global_session:
        return jsonify({"agent_reply": "System is still initializing..."})

    content = genai_types.Content(role='user', parts=[genai_types.Part(text=user_input)])

    # Write to Firestore BEFORE the agent loop
    try:
        # Check if coordinates were passed from frontend, otherwise fallback cleanly
        if req_lat is not None and req_lng is not None:
            base_lat = float(req_lat)
            base_lng = float(req_lng)
        else:
            # Fallback to Yashobhoomi Convention Center base coordinates if permission is denied
            base_lat = 28.5356
            base_lng = 77.0392
        
        # Add a tiny random offset (approx 50-100 meters) so pins spread across the locale
        jitter_lat = random.uniform(-0.0015, 0.0015)
        jitter_lng = random.uniform(-0.0015, 0.0015)
        
        db.collection('keynote_live_traffic').add({
            'lat': base_lat + jitter_lat,
            'lng': base_lng + jitter_lng,
            'timestamp': firestore.SERVER_TIMESTAMP,
            'name': user_name.upper()[:3], # Securely cap at 3 letters
            'mood': user_input[:50] 
        })
    except Exception as e:
        print(f"Non-fatal error writing to Firestore: {e}")

    async def run_agent_loop():
        accumulated_text = []
        try:
            print("\n=== STARTING AGENT RUN ===")
            async for event in runner.run_async(
                new_message=content,
                user_id=USER,
                session_id=global_session.id
            ):
                print(f"[EVENT] Processed ADK Event Type: {type(event).__name__}")
                
                # --- ENHANCED DEBUGGING: Catch function calls in google.genai objects ---
                if hasattr(event, 'content') and event.content and hasattr(event.content, 'parts'):
                    for part in event.content.parts:
                        if hasattr(part, 'function_call') and part.function_call:
                            print(f"[TOOL CALL ALERT] Agent is invoking: {part.function_call.name} with args: {part.function_call.args}")
                        if hasattr(part, 'function_response') and part.function_response:
                            print(f"[TOOL RESPONSE TRACE] MCP Server returned: {part.function_response.response}")
                
                # Legacy check for ADK wrapper data
                if hasattr(event, 'data'):
                    if hasattr(event.data, 'tool_calls') and event.data.tool_calls:
                        print(f"[TOOL CALL ALERT] Agent is invoking: {event.data.tool_calls}")
                    if hasattr(event.data, 'tool_responses') and event.data.tool_responses:
                        print(f"[TOOL RESPONSE TRACE] MCP Server returned: {event.data.tool_responses}")
                # ------------------------------------------------------------------------

                # Extract Text 
                if hasattr(event, 'text') and event.text:
                    accumulated_text.append(event.text)
                elif hasattr(event, 'content') and event.content:
                    if hasattr(event.content, 'parts') and event.content.parts:
                        for part in event.content.parts:
                            if hasattr(part, 'text') and part.text:
                                accumulated_text.append(part.text)
                elif hasattr(event, 'data') and hasattr(event.data, 'message') and event.data.message:
                    accumulated_text.append(str(event.data.message))

            print("=== AGENT RUN COMPLETE ===\n")
            final_reply = "".join(accumulated_text).strip()
            return final_reply
            
        except Exception as e:
            print(f"=== ADK RUNNER ERROR ===\n{e}\n===")
            import traceback
            traceback.print_exc()
            return f"Agent encountered an error: {str(e)}"

    try:
        reply = asyncio.run(run_agent_loop())
        if not reply:
            reply = "I completed the request, but the text response was empty. Please check the backend console."
        print("***********************")
        print(reply)
        return jsonify({"agent_reply": reply})
    except Exception as e:
        print(f"=== FLASK ROUTE ERROR ===\n{e}\n===")
        return jsonify({"agent_reply": "Internal server error. Please check backend logs."}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8081))
    app.run(host='0.0.0.0', port=port, debug=False)
