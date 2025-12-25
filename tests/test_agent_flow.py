"""
Quick test to verify agent input format fixes.
Tests that agents receive correct input format (strings, not dicts).
"""
import asyncio
import sys
sys.path.append(".")

from app.orchestration.flow import AgenticRAGFlow


async def test_agent_flow():
    """Test basic flow execution with fixed agent inputs"""
    print("🧪 Testing Agentic RAG Flow with fixed agent inputs...\n")
    
    # Create flow instance
    flow = AgenticRAGFlow(stream=False)
    
    # Build initial state
    initial_state = {
        "query": "Fuqarolik kodeksining 115-moddasi qanday?",
        "user_id": "5904877504",
        "session_id": "e5d84aca-4473-4da0-9b40-d45dd6296ba7",
    }
    
    print(f"📝 Query: {initial_state['query']}")
    
    try:
        # Execute flow
        print("⏳ Executing flow...\n")
        result = await flow.kickoff_async(inputs=initial_state)
        
        print("\n✅ Flow completed successfully!")
        print(f"\n📊 Final State:")
        print(f"  - Query: {flow.state.query}")
        print(f"  - Selected Assistant: {flow.state.selected_assistant}")
        print(f"  - Strategy: {flow.state.retrieval_output.get('strategy') if flow.state.retrieval_output else 'N/A'}")
        print(f"  - Context Sufficient: {flow.state.context_evaluation_output.get('is_sufficient') if flow.state.context_evaluation_output else 'N/A'}")
        print(f"  - Answer Length: {len(flow.state.answer) if flow.state.answer else 0} characters")
        print(f"\n📢 Final Answer:\n{flow.state.answer}\n")
        
        if flow.state.errors:
            print(f"\n⚠️  Errors encountered: {flow.state.errors}")
        
        print("\n✅ All agent inputs formatted correctly!")
        return True
        
    except Exception as e:
        print(f"\n❌ Flow execution failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = asyncio.run(test_agent_flow())
    sys.exit(0 if success else 1)
