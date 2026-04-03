import sys
import os
import subprocess

# 设置默认编码为UTF-8
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

from flask import Flask, request, jsonify
from flask_cors import CORS

# 添加当前目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# 导入agent
from agent import agent

app = Flask(__name__)
CORS(app)

@app.route('/api/chat', methods=['POST'])
def chat():
    try:
        data = request.get_json()
        user_input = data.get('message')
        
        if not user_input:
            return jsonify({'error': 'No message provided'}), 400
        
        # 调用agent处理消息
        result = agent.invoke(
            {"messages": [{"role": "user", "content": user_input}]},
            {"configurable": {"thread_id": "1"}},
        )
        
        # 获取AI回复
        ai_reply = result["messages"][-1].content
        
        return jsonify({'reply': ai_reply})
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # 启动chroma.py子进程
    # chroma_process = subprocess.Popen(
    #     ['python', 'chroma.py', '--source', './workspace', '--db', './chroma_db'],
    #     stdout=subprocess.PIPE,
    #     stderr=subprocess.PIPE,
    #     text=True
    # )
    # print("ChromaDB索引服务已启动")
    
    # 启动Flask应用
    app.run(host='0.0.0.0', port=5000, debug=True)
