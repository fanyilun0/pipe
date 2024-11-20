import logging
import json
import aiohttp
import asyncio
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BASE_URL = "https://pipe-network-backend.pipecanary.workers.dev/api"

# 心跳和测试节点的间隔时间（单位：秒）
HEARTBEAT_INTERVAL = 5 * 60  # 5分钟
TEST_INTERVAL = 30 * 60  # 30分钟
RETRY_DELAY = 5  # 重试延迟（秒）

async def load_token():
    """从token.txt文件中加载token"""
    try:
        with open('token.txt', 'r') as file:
            token = file.read().strip()
            return token
    except FileNotFoundError:
        logging.error("token.txt文件未找到")
    except Exception as e:
        logging.error(f"从token.txt文件加载token时发生错误: {e}")
    return None

async def get_ip():
    """获取IP地址"""
    async with aiohttp.ClientSession() as session:
        for _ in range(3):  # 尝试三次获取IP
            try:
                async with session.get("https://api64.ipify.org?format=json", timeout=5) as ip_response:
                    if ip_response.status == 200:
                        ip_data = await ip_response.json()
                        return ip_data.get('ip')
            except Exception as e:
                logging.error(f"获取IP失败，正在重试: {e}")
            await asyncio.sleep(RETRY_DELAY)
    logging.error("获取IP地址失败")
    return None

async def send_heartbeat(token, username):
    """发送心跳"""
    try:
        ip = await get_ip()
        if not ip:
            return

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        data = {
            "username": username,
            "ip": ip,
        }
        async with aiohttp.ClientSession() as session:
            for _ in range(3):  # 尝试三次发送心跳
                try:
                    async with session.post(f"{BASE_URL}/heartbeat", headers=headers, json=data, timeout=5) as response:
                        if response.status == 200:
                            logging.info("心跳发送成功")
                            return
                        else:
                            error_message = await response.text()
                            logging.error(f"发送心跳失败。状态: {response.status}, 错误信息: {error_message}")
                except Exception as e:
                    logging.error(f"发送心跳时发生错误，正在重试: {e}")
                await asyncio.sleep(RETRY_DELAY)
        logging.error("心跳发送失败")
    except Exception as e:
        logging.error(f"发送心跳时发生错误: {e}")

async def fetch_points(token):
    """获取分数"""
    headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession() as session:
        for _ in range(3):  # 尝试三次获取分数
            try:
                async with session.get(f"{BASE_URL}/points", headers=headers, timeout=5) as response:
                    logging.info(f"获取分数响应状态: {response.status}")  # 增加日志记录
                    if response.status == 200:
                        data = await response.json()
                        return data.get('points', None)
                    else:
                        error_message = await response.text()
                        logging.error(f"获取分数失败。状态: {response.status}, 错误信息: {error_message}")
            except Exception as e:
                logging.error(f"获取分数时发生错误，正在重试: {e}")
            await asyncio.sleep(RETRY_DELAY)
    logging.error("获取分数失败")
    return None

async def start_testing(token):
    """开始测试节点"""
    logging.info("正在测试节点...")
    async with aiohttp.ClientSession() as session:
        for _ in range(3):  # 尝试三次获取节点信息
            try:
                async with session.get(f"{BASE_URL}/nodes", headers={"Authorization": f"Bearer {token}"}, timeout=5) as response:
                    if response.status == 200:
                        nodes = await response.json()
                        tasks = [test_node(token, node) for node in nodes]
                        await asyncio.gather(*tasks)
                        return
                    else:
                        error_message = await response.text()
                        logging.error(f"获取节点信息时发生错误。状态: {response.status}, 错误信息: {error_message}")
            except Exception as e:
                logging.error(f"获取节点信息时发生错误，正在重试: {e}")
            await asyncio.sleep(RETRY_DELAY)
        logging.error("获取节点信息失败")

async def test_node(token, node):
    """测试单个节点"""
    try:
        start = asyncio.get_event_loop().time()
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://{node['ip']}", timeout=10) as node_response:
                latency = asyncio.get_event_loop().time() - start
                status = "online" if node_response.status == 200 else "offline"
                latency_value = latency if status == "online" else -1
                logging.info(f"节点 {node['node_id']} 测试 ({node['ip']}) latency: {latency_value:.6f}ms, status: {status}")
                await report_node_result(token, node['node_id'], latency_value, status)
    except (asyncio.TimeoutError, aiohttp.ClientConnectorError) as e:
        logging.info(f"节点 {node['node_id']} 测试 ({node['ip']}) latency: -1ms, status: offline")
        logging.error(f"测试节点 {node['ip']} 时发生错误: {e}")
        await report_node_result(token, node['node_id'], -1, "offline")

async def report_node_result(token, node_id, latency, status):
    """报告节点测试结果"""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    test_data = {
        "node_id": node_id,
        "latency": latency,
        "status": status
    }
    async with aiohttp.ClientSession() as session:
        for _ in range(3):  # 尝试三次报告节点结果
            try:
                async with session.post(f"{BASE_URL}/test", headers=headers, json=test_data, timeout=5) as test_response:
                    if test_response.status != 200:
                        error_message = await test_response.text()
                        logging.error(f"报告节点 {node_id} 结果失败。状态: {test_response.status}, 错误信息: {error_message}")
                    else:
                        logging.info(f"节点 {node_id} 结果报告成功")
                    return
            except Exception as e:
                logging.error(f"报告节点 {node_id} 结果时发生错误，正在重试: {e}")
            await asyncio.sleep(RETRY_DELAY)
        logging.error(f"报告节点 {node_id} 结果失败")

async def main():
    print("""
*****************************************************
*           X:https://x.com/ferdie_jhovie           *
*           Tg:https://t.me/sdohuajia               *
*****************************************************
""")
    
    token = await load_token()
    if token:
        logging.info("Token加载成功!")
        
        current_points = await fetch_points(token)
        logging.info(f"当前分数: {current_points}")
        
        next_heartbeat_time = datetime.now()
        next_test_time = datetime.now()
        
        while True:
            current_time = datetime.now()
            
            # 发送心跳
            if current_time >= next_heartbeat_time:
                await send_heartbeat(token, "用户名")  # 这里需要替换为实际的用户名
                next_heartbeat_time = current_time + timedelta(seconds=HEARTBEAT_INTERVAL)
            
            # 测试节点
            if current_time >= next_test_time:
                await start_testing(token)
                # 测试节点完成后获取并显示一次当前分数
                current_points = await fetch_points(token)
                if current_points is not None:
                    logging.info(f"测试节点循环完成后当前分数: {current_points}")
                else:
                    logging.error("获取分数失败")
                next_test_time = current_time + timedelta(seconds=TEST_INTERVAL)
            
            # 等待下一个事件触发
            await asyncio.sleep(1)
    else:
        logging.error("无法加载token。请确保token.txt文件存在且包含有效的token。")

if __name__ == "__main__":
    asyncio.run(main())
