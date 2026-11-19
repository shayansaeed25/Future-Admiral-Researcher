"""Dedicated macro analyst, separate from the global-news tool."""
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tradingagents.agents.utils.agent_utils import get_global_news
from tradingagents.agents.utils.macro_data_tools import get_macro_indicators


def create_macro_analyst(llm):
    tools = [get_macro_indicators, get_global_news]
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are the dedicated macro analyst. Assess rates, inflation, growth, liquidity and scheduled macro risks. Use tools for current FRED data and global news; distinguish observed data from interpretation, state missing data, and explain likely implications for this asset. Do not issue a trade recommendation."),
        MessagesPlaceholder(variable_name="messages"),
    ])
    return prompt | llm.bind_tools(tools)
