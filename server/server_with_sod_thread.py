

#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
@File    : server_with_sod_thread.py
@Author  : wll
@Time    : 2025-07-19
@Version : 1.0
@Desc    : 双模态图像处理服务器(多线程版)，接收可见光/红外图像对并返回推理结果
"""

import glob
import json
import socket
import os
import logging
import subprocess
import threading  # 新增多线程支持

from utils.config import *


def run_damsdet_inference(visible_path, thermal_path):
    """运行 DAMSDet 推理"""
    try:
        output_dir = "/home/wulilin/PycharmProjects/Socket_Conmmunication_SOD/thread_output"
        os.makedirs(output_dir, exist_ok=True)

        cmd = [
            "python",
            "/home/wulilin/projects/DAMSDet-master/tools/multi_infer.py",
            "-c", "/home/wulilin/projects/DAMSDet-master/configs/damsdet/damsdet_r50vd_rgbt.yml",
            f"--infer_vis_img={visible_path}",
            f"--infer_ir_img={thermal_path}",
            f"--output_dir={output_dir}",
            "-o", "weights=/home/wulilin/projects/DAMSDet-master/output/baseline20250117/damsdet_r50vd_rgbt/best_model"
        ]
        logger.info(f"运行推理命令: {' '.join(cmd)}")
        print(f"运行推理命令: {' '.join(cmd)}")

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error(f"推理失败: {result.stderr}")
            return None

        base_name = os.path.splitext(os.path.basename(visible_path))[0]
        ir_result_pattern = os.path.join(output_dir, f"{base_name}_ir.*")
        vis_result_pattern = os.path.join(output_dir, f"{base_name}_vis.*")

        ir_results = glob.glob(ir_result_pattern)
        vis_results = glob.glob(vis_result_pattern)

        if ir_results and vis_results:
            logger.info(f"推理结果图像: {ir_results[0]}, {vis_results[0]}")
            return ir_results[0], vis_results[0]
        else:
            logger.error("推理结果图像不存在")
            return None, None

    except Exception as e:
        logger.error(f"推理出错: {str(e)}")
        return None, None


HEARTBEAT_INTERVAL = 30
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('../logs/server_multithread_seccess0723.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def save_image(image_type, filename, data, save_dir):
    """保存接收到的图像"""
    os.makedirs(os.path.join(save_dir, image_type), exist_ok=True)
    filepath = os.path.join(save_dir, image_type, filename)
    with open(filepath, 'wb') as f:
        f.write(data)
    logger.info(f"{image_type}图像已保存: {filepath}")


def handle_client(conn, addr, save_dir):
    """处理客户端连接的线程函数"""
    logger.info(f"线程{threading.current_thread().name} 开始处理客户端 {addr}")
    try:
        conn.settimeout(HEARTBEAT_INTERVAL + 10)

        client_info = conn.recv(1024).decode()
        if client_info.startswith("CLIENT:"):
            client_ip = client_info.split(":")[1]
            logger.info(f"客户端 {addr} 身份验证通过: {client_ip}")
            conn.sendall(b"ACCEPTED")
        else:
            logger.warning(f"客户端 {addr} 身份验证失败")
            conn.close()
            return

        while True:
            try:
                header = conn.recv(1024).decode()
                if not header:
                    logger.info(f"客户端 {addr} 断开连接")
                    break

                if header == "HEARTBEAT":
                    conn.sendall(b"ALIVE")
                    continue

                if header.startswith("IMAGE:"):
                    _, image_type, filename, file_size = header.split(':', 3)
                    file_size = int(file_size)

                    conn.sendall(b"READY")

                    received_data = b''
                    remaining = file_size
                    while remaining > 0:
                        data = conn.recv(min(4096, remaining))
                        if not data:
                            break
                        received_data += data
                        remaining -= len(data)

                    if len(received_data) == file_size:
                        save_image(image_type, filename, received_data, save_dir)
                        conn.sendall(b"RECEIVED")

                        visible_path = os.path.join(save_dir, "visible", filename)
                        thermal_path = os.path.join(save_dir, "thermal", filename)

                        if os.path.exists(visible_path) and os.path.exists(thermal_path):
                            ir_result, vis_result = run_damsdet_inference(visible_path, thermal_path)

                            for result_img in [ir_result, vis_result]:
                                if result_img and os.path.exists(result_img):
                                    with open(result_img, 'rb') as f:
                                        image_data = f.read()

                                    result_filename = os.path.basename(result_img)
                                    conn.sendall(f"RESULT_IMAGE:{result_filename}:{len(image_data)}".encode())

                                    if conn.recv(1024).decode() == "READY":
                                        conn.sendall(image_data)

                                        if conn.recv(1024).decode() == "RECEIVED":
                                            logger.info(f"结果图像 {result_filename} 发送成功")
                                else:
                                    logger.error(f"结果图像 {result_img} 不存在，发送失败")
                                    conn.sendall(b"RESULT_FAILED")

                    else:
                        logger.error("接收文件数据不完整")
                        conn.sendall(b"FAILED")

                else:
                    logger.warning(f"未知指令: {header}")

            except socket.timeout:
                logger.warning(f"客户端 {addr} 通信超时")
                break
            except Exception as e:
                logger.error(f"处理客户端 {addr} 时发生错误: {str(e)}")
                break

    finally:
        conn.close()
        logger.info(f"客户端 {addr} 连接已关闭，线程{threading.current_thread().name} 结束处理")


def start_server(ip, port, save_dir):
    """启动服务器，接收连接并为每个客户端开启线程"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((ip, port))
        s.listen()
        logger.info(f"服务器已启动，监听 {ip}:{port}")

        while True:
            try:
                conn, addr = s.accept()
                logger.info(f"客户端已连接: {addr}")
                client_thread = threading.Thread(target=handle_client, args=(conn, addr, save_dir), daemon=True)
                client_thread.start()
            except Exception as e:
                logger.error(f"服务器错误: {str(e)}")


if __name__ == "__main__":
    os.makedirs(RECEIVED_FOLDER, exist_ok=True)
    start_server(SERVER_IP, SERVER_PORT, RECEIVED_FOLDER)
