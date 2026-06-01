import os
from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver

# 计算器 SubAgent：接收一个数字，乘以 1024 后返回结果
calculator_subagent = {
    "name": "sy_calculator",
    "description": "对给定的一个数字进行运算",
    "system_prompt": "你是一个计算器助手。收到一个数字后，将其乘以 1024 并返回结果。",
}

# 天气 SubAgent：查询全国城市实时天气（模拟）
weather_subagent = {
    "name": "sy_weather",
    "description": "查询全国城市实时天气信息（模拟）",
    "system_prompt": (
        "你是一个模拟天气助手，能根据用户提供的城市名称返回模拟的实时天气信息。"
        "请用中文简洁明了地回复天气情况。"
        "模拟数据包含城市、天气状况、温度（-10°C到50°C）、湿度（30%到90%）等信息，"
        "格式为：\n"
        "城市: [城市名称]\n"
        "天气: [天气状况]\n"
        "温度: [温度]°C\n"
        "湿度: [湿度]%"
    ),
}

deepagents_graph = create_deep_agent(
    model=ChatOpenAI(
        model="MiniMax-M2.5",
        api_key="sk-sp-NDY2LTEyNDY2MzE1NjEwLTE3NzgyOTM0NTAzNDA=",
        base_url="https://api.scnet.cn/api/llm/v1",
    ),
    system_prompt=(
        "你是一个智能AI助手，能够根据用户请求调用相应的SubAgent。"
        "当用户给你一个数字时，使用sy_calculator这个SubAgent；"
        "当用户需要查询天气时，使用sy_weather这个SubAgent，"
        "返回简洁的结果而不是原始数据"
    ),
    subagents=[weather_subagent, calculator_subagent],
    checkpointer=MemorySaver(),
)
