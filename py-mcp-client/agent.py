"""
this script does 3 things:

- Connects to AgentGateway as an MCP client (agentgateway has access to the mcp servers aka the tools)
- Talks to Ollama as the LLM brain
- Runs a loop where you can chat and it uses your mcp tools

process:

1. connect to mcp - establish an SSE connection to agentgateway, do a handshake & get list of tools back
2. talk to ollama (LLM) - send a message to LLM along with the tool list. Get back either a text answer or a tool call request.
3. the conversation loop - keep a conversation going. When LLM asks for a tool, call it, feed the result back, get the final answer.

"""

import asyncio
import json

from mcp import ClientSession
from mcp.client.sse import sse_client
import ollama

AGENT_GATEWAY_URL = "http://127.0.0.1:3001/sse"

def convert_tools_for_ollama(mcp_tools):
    ollama_tools = []
    for tool in mcp_tools:
        ollama_tools.append({
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.inputSchema
            }
        })
    return ollama_tools

async def ask_ollama(messages, tools):
    response = ollama.chat(
        model="llama3.1:8b",
        messages=messages,
        tools=tools
    )
    return response

async def ask_ollama_streaming(messages):
    print("\n\033[1;36mAgent:\033[0m ", end="", flush=True)
    full_content = ""
    
    stream = ollama.chat(
        model="llama3.1:8b",
        messages=messages,
        stream=True
    )
    
    for chunk in stream:
        token = chunk.message.content
        print(token, end="", flush=True)
        full_content += token
    
    print("\n")
    return full_content

async def run_agent():
    print("Connecting to Agent Gateway...")
    
    async with sse_client(AGENT_GATEWAY_URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            
            tools_result = await session.list_tools()
            ollama_tools = convert_tools_for_ollama(tools_result.tools)
            
            print(f"Connected. {len(tools_result.tools)} tools available.\n")
            
            # build a lookup: short name -> full name
            tool_name_map = {}
            for tool in tools_result.tools:
                short = tool.name.split('_', 2)[-1]  # strips prefix
                tool_name_map[short] = tool.name
                tool_name_map[tool.name] = tool.name  # full name maps to itself
            
            messages = []
            print("Agent ready. Type your question or 'quit' to exit.\n")
            
            while True:
                user_input = input("\033[1;32mYou:\033[0m ").strip()
                if user_input.lower() == 'quit':
                    break
                
                messages.append({"role": "user", "content": user_input})
                
                response = await ask_ollama(messages, ollama_tools)
                
                if response.message.tool_calls:
                    messages.append(response.message.model_dump(exclude_none=True))

                    for tool_call in response.message.tool_calls:
                        
                        # resolve short name to full name
                        requested_name = tool_call.function.name
                        full_name = tool_name_map.get(requested_name, requested_name)
                        
                        print(f"\n\033[1;33m[→ calling tool: {full_name}]\033[0m")
                        
                        args = tool_call.function.arguments
                        if isinstance(args, str):
                            args = json.loads(args) if args.strip() else {}

                        # call the tool via Agent Gateway
                        tool_result = await session.call_tool(
                            full_name,
                            args
                        )
                        
                        # add tool result to messages
                        messages.append({
                            "role": "tool",
                            "content": str(tool_result.content),
                            "tool_name": requested_name,
                        })
                    
                    # send result back to Ollama for final answer
                    final_content = await ask_ollama_streaming(messages)
                    messages.append({
                        "role": "assistant",
                        "content": final_content
                    })
                
                else:
                    # no tool needed, stream direct answer
                    direct_content = await ask_ollama_streaming(messages)
                    messages.append({
                        "role": "assistant",
                        "content": direct_content
                    })

if __name__ == "__main__":
    asyncio.run(run_agent())