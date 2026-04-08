import os
import yaml
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langchain.messages import RemoveMessage, SystemMessage
from langchain.agents import create_agent, AgentState
from langchain.agents.middleware import after_model
from langgraph.runtime import Runtime
from langchain_core.runnables import RunnableConfig
import subprocess
from extractText.extract import extract

import requests
from bs4 import BeautifulSoup

from search_tools import search_file as chroma_search
from bm25 import search as bm25_search

#==========================================全局变量==========================================

KEEP_MESSAGE_COUNT = 31  # 总消息阈值
KEEP_RECENT_MESSAGES = 13  # 保留最新的对话数量

# 从环境变量获取API key
DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY")
if not DASHSCOPE_API_KEY:
    raise ValueError("环境变量 DASHSCOPE_API_KEY 未设置")

#===========================================工具===========================================


# @tool
# def get_weather(city: str) -> str:
#     """获取指定城市的天气"""
#     return f"{city} 天气总是晴朗！"

@tool
def read_file(file: str) -> str:
    """
    输入文件路径，返回文件中的文本内容。用于阅读某个文件。
    支持格式:
    PDF       .pdf
    Word      .docx
    Excel     .xlsx  .xlsm  .xls  .ods
    PPT       .pptx
    EPUB      .epub
    纯文本    .txt .md .csv .log .json .xml 等
    """
    print("reading:"+file)
    content = extract(file)
    return content

@tool
def run_powershell(command: str) -> str:
    """
    在用户的电脑上输入 PowerShell 命令，执行并返回结果。
    注意：用户提供的文件路径必须原样使用，不得修改任何字符，包括不能添加空格。
    如果有中文乱码，加上-Encoding UTF8试试。
    """
    print("command:" + command)
    result = subprocess.run(
        ["powershell", "-Command", command],
        capture_output=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("gbk", errors="replace").strip() if result.stderr else "未知错误"
        return f"执行失败：{stderr}"

    output = result.stdout.decode("gbk", errors="replace").strip()
    print("result:" + output)
    return output


@tool
def web_fetch(url: str) -> str:
    """
    输入网站url，获取网页的文本内容。
    当你需要查询网页时可以调用该工具，搜索也可以使用该工具，例如搜索baidu.com后面跟参数。
    """
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        # 解析 HTML，提取纯文本
        soup = BeautifulSoup(response.text, "html.parser")
        
        # 去掉 script / style 标签
        for tag in soup(["script", "style"]):
            tag.decompose()
        
        text = soup.get_text(separator="\n", strip=True)
        
        # 限制长度，避免超出 context
        return text[:3000]

    except Exception as e:
        return f"获取失败: {e}"


@tool
def search_file(question: str) -> str:
    """
    根据用户问题输入优化搜索词，在本地向量数据库中搜索最相关的切片，并返回前10个切片的内容和源文件路径。
    用于快速找到与问题相关的文件和内容。如果切片信息不足，可以调用read_file工具读取文件内容。
    """
    print("searching:"+question)
    result = chroma_search(question)
    print("result:"+result)
    return result


@tool
def search_bm25(question: str, folder: str = None, top_k: int = 5) -> str:
    """
    使用BM25算法在指定文件夹中搜索相关文档，返回文件名和BM25分数。
    如果不指定folder，则默认搜索工作目录。
    用于快速查找与问题相关的文件。
    """
    if folder is None:
        folder = WORKSPACE_DIR
    print(f"bm25 searching: {question} in {folder}")
    try:
        results = bm25_search(question, folder=folder, top_k=top_k)
        if not results:
            return "未找到相关文档"
        
        output = []
        output.append(f"找到 {len(results)} 个相关文档:")
        for filename, score in results:
            output.append(f"  {filename}: {score:.4f}")
        
        return "\n".join(output)
    except Exception as e:
        return f"搜索失败: {e}"


#===========================================中间件===========================================


@after_model
def compress_conversation(state: AgentState, runtime: Runtime) -> dict | None:
    """压缩历史对话：保留最新的对话，总结更早的对话。"""
    messages = state["messages"]
    
    # 如果消息数量超过阈值，需要进行压缩
    if len(messages) > KEEP_MESSAGE_COUNT:
        # 保留最新的KEEP_RECENT_MESSAGES条消息
        recent_messages = messages[-KEEP_RECENT_MESSAGES:]
        
        # 需要总结的历史消息（排除最近的消息）
        history_messages = messages[:-KEEP_RECENT_MESSAGES]
        
        # 将历史消息转换为文本格式，用于总结
        history_text = "\n".join([f"{msg.type}: {msg.content}" for msg in history_messages])
        
        # 使用LLM总结历史对话
        summary_prompt = f"""请总结以下对话历史，保留关键信息和结论：
                        {history_text}
                        总结要求：
                        1. 简洁明了，突出重点
                        2. 保留用户的问题和AI的回答要点
                        3. 不要包含冗余信息
                        4. 使用中文总结
                        """
        
        try:
            # 使用aliyun模型生成总结
            summary_result = aliyun.invoke({
                "messages": [{"role": "user", "content": summary_prompt}]
            })
            summary_content = summary_result["choices"][0]["message"]["content"]
            
            # 创建总结消息
            summary_message = SystemMessage(
                content=f"【对话总结】\n{summary_content}",
                additional_kwargs={"is_summary": True}
            )
            
            # 构建更新操作：删除所有旧消息，添加总结消息和最近消息
            operations = []
            
            # 删除所有旧消息
            for msg in messages:
                operations.append(RemoveMessage(id=msg.id))
            
            # 添加总结消息和最近消息
            operations.append(summary_message)
            operations.extend(recent_messages)
            
            return {"messages": operations}
            
        except Exception as e:
            print(f"总结对话失败: {e}")
            # 如果总结失败，回退到简单删除方案
            return {"messages": [RemoveMessage(id=m.id) for m in messages[:-KEEP_RECENT_MESSAGES]]}
    
    return None

#===========================================架构===========================================

aliyun = init_chat_model(
    model="glm-5",
    model_provider="openai",
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    api_key=DASHSCOPE_API_KEY,
)

WORKSPACE_DIR = os.path.join(os.path.dirname(__file__), "workspace")

agent = create_agent(
    model=aliyun,
    tools=[web_fetch,run_powershell,read_file,search_file,search_bm25],
    system_prompt=f"""
    你是一个在用户电脑本地的ai助手，你的工作目录是{WORKSPACE_DIR}。
    当用户想你发送信息时，你需要先判断是否需要执行任务，如果你认为有执行任务的必要，再去执行。
    当你需要检索信息时，你可以先调用search_file工具查询本地向量数据库，若搜索不到，根据切片的来源文件路径用read_file工具读取文件内容，如果还是没有找到，使用BM25工具搜索相关文件并读取文件内容，最后如果还是没有找到，直接询问用户。
    当你要执行权限比较高的任务时，比如删除文件，一定要先跟用户确认。
    除非有用户允许，你的所有操作要限制在工作目录内。
    每次执行完任务要总结并告知用户自己用了哪些工具干了什么，结果怎么样。
    用户所有问题都要优先使用本地向量数据库搜索，若搜索不到，再考虑使用其他工具。
    """,
    checkpointer=InMemorySaver(),
    middleware=[compress_conversation]
)


#===========================================执行===========================================
if __name__ == "__main__":
    # 执行代理
    while True:
        user_input = input("human: ")
        if user_input.lower() in ["exit", "quit", "q"]:
            print("退出对话")
            break
        
        result = agent.invoke(
            {"messages": [{"role": "user", "content": user_input}]},
            {"configurable": {"thread_id": "1"}},
        )
        print("ai:", result["messages"][-1].content)

    for msg in result["messages"]:
        print(f"{msg.type}: {msg.content}")