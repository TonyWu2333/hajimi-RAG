import sys
import os
import subprocess
import json
import time
import threading

# 设置默认编码为UTF-8
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS

# 添加当前目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# 导入agent
from agent import agent

app = Flask(__name__)

# LangGraph 同步 stream 使用线程池；并发请求或客户端中途断开时，易出现 executor shutdown 后仍 submit 的回调错误。
_agent_stream_lock = threading.Lock()
CORS(app)

def _format_content(content):
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False, indent=2)
    except Exception:
        return str(content)

def _tool_call_args(call):
    """Normalize tool call arguments to a dict for the stream UI."""
    if isinstance(call, dict):
        args = call.get("args")
        if isinstance(args, dict):
            return args
        fn = call.get("function")
        if isinstance(fn, dict):
            raw = fn.get("arguments")
            if isinstance(raw, str):
                try:
                    parsed = json.loads(raw)
                    return parsed if isinstance(parsed, dict) else {}
                except Exception:
                    return {}
        return {}
    args = getattr(call, "args", None)
    return args if isinstance(args, dict) else {}


def _serialize_tool_traces(messages):
    traces = []
    for msg in messages:
        msg_type = getattr(msg, "type", "") or msg.__class__.__name__.lower()

        if msg_type == "ai":
            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls and hasattr(msg, "additional_kwargs"):
                tool_calls = msg.additional_kwargs.get("tool_calls")
            for call in tool_calls or []:
                traces.append({
                    "type": "tool_call",
                    "name": call.get("name", "unknown"),
                    "args": call.get("args", {}),
                })

        if msg_type == "tool":
            traces.append({
                "type": "tool_result",
                "name": getattr(msg, "name", "unknown"),
                "content": _format_content(getattr(msg, "content", "")),
                "status": getattr(msg, "status", "success"),
            })
    return traces

def _iter_agent_events(user_input):
    """
    以 NDJSON 方式输出事件，前端可边读边渲染:
    - tool_start
    - tool_end
    - answer_chunk
    - done
    - error
    """
    tool_started = set()
    tool_finished = set()
    step_seq = 0

    def emit(event):
        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    stream_iter = None
    try:
        with _agent_stream_lock:
            stream_iter = agent.stream(
                {"messages": [{"role": "user", "content": user_input}]},
                {"configurable": {"thread_id": "1"}},
                stream_mode="updates",
            )
            for chunk in stream_iter:
                if not isinstance(chunk, dict):
                    continue
                for _, payload in chunk.items():
                    if not isinstance(payload, dict):
                        continue
                    for msg in payload.get("messages", []) or []:
                        msg_type = getattr(msg, "type", "") or msg.__class__.__name__.lower()

                        if msg_type == "ai":
                            tool_calls = getattr(msg, "tool_calls", None)
                            if not tool_calls and hasattr(msg, "additional_kwargs"):
                                tool_calls = msg.additional_kwargs.get("tool_calls")
                            response_meta = getattr(msg, "response_metadata", {}) or {}
                            finish_reason = response_meta.get("finish_reason")
                            text_chunk = getattr(msg, "content", "")

                            # 先推送思考文本，再推送工具调用，前端才能「先思考、后工具」
                            if isinstance(text_chunk, str) and text_chunk.strip():
                                is_thinking_step = bool(tool_calls) or finish_reason == "tool_calls"
                                cut = 16
                                if is_thinking_step:
                                    step_id = f"step_{step_seq}"
                                    step_seq += 1
                                    yield emit({"type": "step_start", "step_id": step_id})
                                    for i in range(0, len(text_chunk), cut):
                                        part = text_chunk[i:i + cut]
                                        yield emit({"type": "step_chunk", "step_id": step_id, "content": part})
                                        time.sleep(0.01)
                                    yield emit({"type": "step_done", "step_id": step_id})
                                else:
                                    for i in range(0, len(text_chunk), cut):
                                        part = text_chunk[i:i + cut]
                                        yield emit({"type": "answer_chunk", "content": part})
                                        time.sleep(0.01)

                            for call in tool_calls or []:
                                if isinstance(call, dict):
                                    call_name = call.get("name")
                                    if not call_name:
                                        fn = call.get("function")
                                        if isinstance(fn, dict):
                                            call_name = fn.get("name", "工具")
                                    call_name = call_name or "工具"
                                    call_id = call.get("id") or f"{call_name}_{len(tool_started)}"
                                else:
                                    call_name = getattr(call, "name", "工具")
                                    call_id = getattr(call, "id", None) or f"{call_name}_{len(tool_started)}"
                                if call_id in tool_started:
                                    continue
                                tool_started.add(call_id)
                                yield emit({
                                    "type": "tool_start",
                                    "call_id": call_id,
                                    "name": call_name,
                                    "args": _tool_call_args(call),
                                })

                        elif msg_type == "tool":
                            call_id = getattr(msg, "tool_call_id", None) or f"{getattr(msg, 'name', 'unknown')}_{len(tool_finished)}"
                            if call_id not in tool_finished:
                                tool_finished.add(call_id)
                                yield emit({
                                    "type": "tool_end",
                                    "call_id": call_id,
                                    "name": getattr(msg, "name", "unknown"),
                                    "status": "success",
                                })

            yield emit({"type": "done"})

    except Exception as e:
        err_text = str(e)
        yield emit({"type": "error", "message": err_text})
        yield emit({"type": "done"})
    finally:
        if stream_iter is not None:
            try:
                stream_iter.close()
            except Exception:
                pass

@app.route('/api/chat', methods=['POST'])
def chat():
    try:
        data = request.get_json()
        user_input = data.get('message')
        
        if not user_input:
            return jsonify({'error': 'No message provided'}), 400
        
        # 与流式接口共用锁，避免与 stream 并行触发 LangGraph 线程池竞态
        with _agent_stream_lock:
            result = agent.invoke(
                {"messages": [{"role": "user", "content": user_input}]},
                {"configurable": {"thread_id": "1"}},
            )
        
        # 获取AI回复
        ai_reply = result["messages"][-1].content
        traces = _serialize_tool_traces(result["messages"])
        
        return jsonify({'reply': ai_reply, 'traces': traces})
    
    except Exception as e:
        err_text = str(e)
        # 把错误转成可展示的回复，前端可直接显示
        return jsonify({'reply': f'⚠️ 后端执行出错：{err_text}', 'error': err_text, 'traces': []}), 500

@app.route('/api/chat/stream', methods=['POST'])
def chat_stream():
    data = request.get_json(silent=True) or {}
    user_input = data.get('message')
    if not user_input:
        return jsonify({'error': 'No message provided'}), 400

    return Response(
        stream_with_context(_iter_agent_events(user_input)),
        mimetype='text/event-stream',
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

if __name__ == '__main__':
    # debug 且默认 use_reloader=True 时会双进程，易与 LangGraph 线程池交错；本地开发可关 reloader 减少偶发 executor 错误
    # 启动chroma.py子进程
    # chroma_process = subprocess.Popen(
    #     ['python', 'chroma.py', '--source', './workspace', '--db', './chroma_db'],
    #     stdout=subprocess.PIPE,
    #     stderr=subprocess.PIPE,
    #     text=True
    # )
    # print("ChromaDB索引服务已启动")
    
    # 启动Flask应用
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)
