"""
search_tools.py - ChromaDB搜索工具 with Rerank
"""

import os
import requests
from pathlib import Path

try:
    import chromadb
except ImportError:
    pass

from chroma import get_collection


def search_file(question: str, top_n: int = 10, rerank_threshold: float = None) -> str:
    """
    输入搜索问题，在ChromaDB中搜索最相关的切片，然后使用阿里云Rerank模型对结果重排，
    返回重排后的前top_n个切片的内容和源文件路径。

    参数:
        question: 搜索问题
        top_n: 重排后返回的结果数量（默认5）
        rerank_threshold: 可选，只返回重排分数高于此阈值的结果
    """
    db_path = os.path.join(os.path.dirname(__file__), "chroma_db")
    
    try:
        # 1. 从ChromaDB召回更多候选（例如20个，供重排筛选）
        collection = get_collection(db_path)
        candidate_results = collection.query(
            query_texts=[question],
            n_results=20,  # 先召回20个候选，供重排模型筛选
            include=["documents", "metadatas", "distances"]
        )
        
        if not candidate_results or not candidate_results["documents"] or not candidate_results["documents"][0]:
            return "未找到相关内容"
        
        documents = candidate_results["documents"][0]
        metadatas = candidate_results["metadatas"][0]
        chroma_distances = candidate_results["distances"][0]
        
        # 2. 调用阿里云Rerank API进行精排
        reranked = call_rerank_api(question, documents)
        
        if not reranked:
            # 如果重排失败，退回到原始Chroma排序结果
            return format_results(documents, metadatas, chroma_distances, top_n, use_rerank=False)
        
        # 3. 根据重排结果筛选并返回
        return format_reranked_results(reranked, metadatas, documents, top_n, rerank_threshold)
        
    except Exception as e:
        return f"搜索失败: {str(e)}"


def call_rerank_api(query: str, documents: list) -> list:
    """
    调用阿里云DashScope的重排API
    
    返回格式:
    [
        {"index": 0, "relevance_score": 0.98},
        {"index": 2, "relevance_score": 0.75},
        ...
    ]
    """
    if not documents or len(documents) == 0:
        return None
    
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        # 如果没有设置API Key，返回None，让调用方回退到Chroma排序
        print("Warning: DASHSCOPE_API_KEY not set, falling back to Chroma ranking")
        return None
    
    url = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    # 修复：top_n 必须大于0，且不能超过documents数量
    rerank_top_n = min(len(documents), 30)  # 至少返回1个，最多20个
    
    payload = {
        "model": "qwen3-rerank",  # 使用gte-rerank模型，更稳定
        "input": {
            "query": query,
            "documents": documents
        },
        "parameters": {
            "return_documents": False,  # 不返回文档原文以节省带宽，我们已有原文
            "top_n": rerank_top_n if rerank_top_n > 0 else 1  # 确保top_n >= 1
        }
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        if response.status_code == 200:
            data = response.json()
            # 解析返回的排序结果
            results = data.get("output", {}).get("results", [])
            if not results:
                print("Rerank API returned no results")
                return None
            # 按重排分数降序排序
            sorted_results = sorted(results, key=lambda x: x.get("relevance_score", 0), reverse=True)
            return sorted_results
        else:
            print(f"Rerank API error: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        print(f"Rerank API exception: {e}")
        return None


def format_reranked_results(reranked_results: list, metadatas: list, documents: list, 
                           top_n: int, threshold: float = None) -> str:
    """格式化重排后的结果"""
    if not reranked_results:
        return "重排后未找到相关内容"
    
    result_str = "✅ 重排后的相关内容：\n\n"
    count = 0
    for item in reranked_results:
        if count >= top_n:
            break
        
        idx = item.get("index")
        score = item.get("relevance_score", 0.0)
        
        if threshold is not None and score < threshold:
            continue
        
        if idx is None or idx >= len(documents):
            print(f"Warning: Invalid index {idx} in rerank results")
            continue
        
        doc = documents[idx]
        metadata = metadatas[idx] if idx < len(metadatas) else None
        file_path = metadata.get("file_path", "未知文件") if metadata else "未知文件"
        chunk_index = metadata.get("chunk_index", 0) if metadata else 0
        
        result_str += f"[{count+1}] 文件: {file_path}\n"
        result_str += f"    切片索引: {chunk_index}\n"
        result_str += f"    重排分数: {score:.4f}\n"
        result_str += f"    内容预览: {doc[:200]}{'...' if len(doc) > 200 else ''}\n"
        result_str += "-" * 80 + "\n"
        count += 1
    
    if count == 0:
        return "没有符合阈值要求的结果"
    return result_str


def format_results(documents: list, metadatas: list, distances: list, 
                   top_n: int, use_rerank: bool = False) -> str:
    """原始的Chroma排序结果格式化（作为重排失败时的回退）"""
    title = "⚠️ 使用Chroma原始排序（重排服务不可用）：\n\n" if not use_rerank else "Chroma排序结果：\n\n"
    result_str = title
    for i in range(min(top_n, len(documents))):
        doc = documents[i]
        metadata = metadatas[i] if i < len(metadatas) else None
        distance = distances[i] if i < len(distances) else None
        
        file_path = metadata.get("file_path", "未知文件") if metadata else "未知文件"
        chunk_index = metadata.get("chunk_index", 0) if metadata else 0
        
        result_str += f"[{i+1}] 文件: {file_path}\n"
        result_str += f"    切片索引: {chunk_index}\n"
        if distance is not None:
            result_str += f"    Chroma相似度: {1 - distance:.4f}\n"
        result_str += f"    内容预览: {doc[:200]}{'...' if len(doc) > 200 else ''}\n"
        result_str += "-" * 80 + "\n"
    return result_str

result = search_file("竞品分析")
print(result)