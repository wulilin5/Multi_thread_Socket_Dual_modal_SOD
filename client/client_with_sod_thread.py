#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
@File    : client_with_dsod_success_threaded.py
@Author  : wll
@Time    : 2025-07-19
@Version : 1.0
@Desc    : 双模态图像传输客户端，多线程支持同时发送多对图像及接收结果
"""

import socket
import os
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from utils.config import *

HEARTBEAT_INTERVAL = 30  # 心跳间隔(秒)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('../logs/client_multithread_success0723.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def validate_image_path(path, image_type):
    if not os.path.exists(path):
        logger.error(f"{image_type}图像不存在: {path}")
        return False
    if not path.lower().endswith('.jpg'):
        logger.warning(f"{image_type}图像不是JPG格式: {path}")
    return True


def send_single_image(sock, image_type, file_path):
    try:
        sock.settimeout(30.0)

        # 发送心跳包确认连接
        sock.sendall(b"HEARTBEAT")
        if sock.recv(1024).decode() != "ALIVE":
            logger.error("服务器未响应心跳")
            return False

        filename = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)

        sock.sendall(f"IMAGE:{image_type}:{filename}:{file_size}".encode())
        if sock.recv(1024).decode() != "READY":
            logger.error("服务器未准备好接收")
            return False

        with open(file_path, 'rb') as f:
            total_sent = 0
            while total_sent < file_size:
                data = f.read(4096)
                sent = sock.send(data)
                if sent == 0:
                    logger.error("发送中断")
                    return False
                total_sent += sent

        confirmation = sock.recv(1024).decode()
        if confirmation != "RECEIVED":
            logger.error(f"{image_type}图像发送未确认")
            return False

        logger.info(f"{image_type}图像发送成功: {filename}")
        return True

    except socket.timeout:
        logger.error("发送超时")
        return False
    except Exception as e:
        logger.error(f"发送{image_type}图像出错: {str(e)}")
        return False


def send_image_pair(pair_name):
    """单个线程发送一对图像"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(10.0)
            s.bind((CLIENT_IP, 0))
            s.connect((SERVER_IP, SERVER_PORT))
            logger.info(f"线程{pair_name}: 已连接到服务器 {SERVER_IP}:{SERVER_PORT}")

            s.sendall(f"CLIENT:{CLIENT_IP}".encode())
            if s.recv(1024).decode() != "ACCEPTED":
                logger.error(f"线程{pair_name}: 服务器拒绝连接")
                return False

            last_heartbeat = time.time()

            visible_path = os.path.join(VISIBLE_FOLDER, f"{pair_name}.jpg")
            thermal_path = os.path.join(THERMAL_FOLDER, f"{pair_name}.jpg")

            if not validate_image_path(visible_path, "可见光") or not validate_image_path(thermal_path, "红外"):
                return False

            if not send_single_image(s, "visible", visible_path):
                return False
            if not send_single_image(s, "thermal", thermal_path):
                return False

            for _ in range(2):  # 接收两张结果图
                response = s.recv(1024).decode()
                if response.startswith("RESULT_IMAGE:"):
                    _, filename, file_size = response.split(':')
                    file_size = int(file_size)

                    s.sendall(b"READY")

                    received_data = b''
                    remaining = file_size
                    while remaining > 0:
                        data = s.recv(min(4096, remaining))
                        if not data:
                            break
                        received_data += data
                        remaining -= len(data)

                    if len(received_data) == file_size:
                        os.makedirs("../results_thread", exist_ok=True)
                        result_path = os.path.join("../results_thread", filename)
                        with open(result_path, 'wb') as f:
                            f.write(received_data)
                        s.sendall(b"RECEIVED")
                        logger.info(f"线程{pair_name}: 推理结果已保存: {result_path}")
                    else:
                        logger.error(f"线程{pair_name}: 接收结果图像不完整")
                        return False
                elif response == "RESULT_FAILED":
                    logger.error(f"线程{pair_name}: 服务器推理失败")
                    return False

            return True

    except Exception as e:
        logger.error(f"线程{pair_name}: 出错: {str(e)}")
        return False


def main():
    while True:
        msg = input("请输入 'send pair' or 'quit': > ")
        if msg == 'quit':
            break
        elif msg == 'send pair':
            pair_names_str = input("输入多个图像对名称（用空格分隔）: ")
            pair_names = pair_names_str.strip().split()

            if not pair_names:
                logger.warning("没有输入任何图像对名称")
                continue

            max_workers = min(len(pair_names), 10)  # 最多10线程，避免过多资源占用
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(send_image_pair, name): name for name in pair_names}

                for future in as_completed(futures):
                    name = futures[future]
                    try:
                        success = future.result()
                        if success:
                            logger.info(f"线程{name}: 图像对发送完成")
                        else:
                            logger.error(f"线程{name}: 图像对发送失败")
                    except Exception as e:
                        logger.error(f"线程{name}: 出现异常: {str(e)}")


if __name__ == "__main__":
    main()
