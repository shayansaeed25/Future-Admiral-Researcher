from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tradingagents.agents.utils.order_flow_tools import get_order_flow_metrics
from tradingagents.agents.utils.agent_utils import get_instrument_context_from_state, get_language_instruction

def create_orderflow_analyst(llm):
    tools = [get_order_flow_metrics]
    system_prompt = (
        "You are an Institutional Order Flow & Crypto Derivatives Analyst. "
        "Your task is to analyze real-time market data: Cumulative Volume Delta (CVD), Open Interest (OI), "
        "Funding Rates, and Order Book Liquidity Imbalance. Identify traps, potential long/short squeezes, "
        "and whale absorption. State your technical conclusion directly."
    )
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt + " For quantitative claims, call the tool and flag unavailable feed fields. " + get_language_instruction()),
        MessagesPlaceholder(variable_name="messages"),
    ])
    return prompt | llm.bind_tools(tools)

def orderflow_analyst_node(state, llm):
    chain = create_orderflow_analyst(llm)
    messages = state["messages"]
    result = chain.invoke(messages)
    return {"messages": [result]}
