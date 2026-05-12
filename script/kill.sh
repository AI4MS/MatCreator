#!/bin/bash

# 要关闭的端口
PORTS=(8000 8001 5173)

echo "正在查找并关闭端口: ${PORTS[*]}"

for port in "${PORTS[@]}"; do
    # 查找占用端口的进程 PID
    PID=$(lsof -t -i :$port)
    
    if [ -n "$PID" ]; then
        echo "端口 $port 被 PID $PID 占用，正在关闭..."
        kill -9 $PID
        echo "✅ 已杀死 PID $PID (端口 $port)"
    else
        echo "✅ 端口 $port 未被占用"
    fi
done

echo -e "\n所有指定端口已清理完成！"