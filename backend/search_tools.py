#!/usr/bin/env python3
"""
search_tools.py - ChromaDB搜索工具
"""

import os
from pathlib import Path

try:
    import chromadb
except ImportError:
    pass

from chroma import get_collection


def search_file(question: str) -> str:
    """
    输入搜索问题，在ChromaDB中搜索最相关的切片，并返回前10个切片的内容和源文件路径。
    用于快速找到与问题相关的文件和内容。
    """
    db_path = os.path.join(os.path.dirname(__file__), "chroma_db")
    
    try:
        # 获取ChromaDB集合
        collection = get_collection(db_path)
        
        # 搜索最相关的切片
        results = collection.query(
            query_texts=[question],
            n_results=10,
            include=["documents", "metadatas", "distances"]
        )
        
        if not results or not results["documents"]:
            return "未找到相关内容"
        
        # 构建结果字符串
        result_str = "找到相关内容：\n\n"
        for i, (doc, metadata, distance) in enumerate(
            zip(results["documents"][0], results["metadatas"][0], results["distances"][0]),
            1
        ):
            file_path = metadata.get("file_path", "未知文件") if metadata else "未知文件"
            chunk_index = metadata.get("chunk_index", 0) if metadata else 0
            
            result_str += f"[{i}] 文件: {file_path}\n"
            result_str += f"    切片索引: {chunk_index}\n"
            result_str += f"    相似度: {1 - distance:.4f}\n"
            result_str += f"    内容: {doc}\n"
            result_str += "-" * 80 + "\n"
        
        return result_str
        
    except Exception as e:
        return f"搜索失败: {str(e)}"
