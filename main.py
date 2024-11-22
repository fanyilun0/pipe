import logging
import json
import aiohttp
import asyncio
from datetime import datetime, timedelta

# ANSI 转义序列
class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    RESET = "\033[0m"

# 基础配置
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
BASE_URL = "https://pipe-network-backend.pipecanary.workers.dev/api"

# 时间间隔配置
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
    """获取当前IP地址"""
    async with aiohttp.ClientSession() as session:
        for _ in range(3):
            try:
                async with session.get("https://api64.ipify.org?format=json", timeout=5) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get('ip')
            except Exception as e:
                logging.error(f"获取IP失败，正在重试: {e}")
                await asyncio.sleep(RETRY_DELAY)
    return None

async def send_heartbeat(token, username):
    """发送心跳信号"""
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
        for _ in range(3):
            try:
                async with session.post(f"{BASE_URL}/heartbeat", headers=headers, json=data, timeout=5) as response:
                    if response.status == 200:
                        logging.info("心跳发送成功")
                        return
                    error_message = await response.text()
                    logging.error(f"发送心跳失败。状态: {response.status}, 错误信息: {error_message}")
            except Exception as e:
                logging.error(f"发送心跳时发生错误，正在重试: {e}")
                await asyncio.sleep(RETRY_DELAY)

async def fetch_points(token):
    """获取当前分数"""
    headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession() as session:
        for _ in range(3):
            try:
                async with session.get(f"{BASE_URL}/points", headers=headers, timeout=5) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get('points')
            except Exception as e:
                logging.error(f"获取分数时发生错误，正在重试: {e}")
                await asyncio.sleep(RETRY_DELAY)
    return None

async def test_all_nodes(nodes):
    """同时测试所有节点"""
    async def test_single_node(node):
        try:
            start = asyncio.get_event_loop().time()
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://{node['ip']}", timeout=5) as node_response:
                    latency = (asyncio.get_event_loop().time() - start) * 1000
                    status = "在线" if node_response.status == 200 else "离线"
                    latency_value = latency if status == "在线" else -1
                    logging.info(f"节点 {node['node_id']} 测试 ({node['ip']}) latency: {latency_value:.2f}ms, status: {status}")
                    return (node['node_id'], node['ip'], latency_value, status)
        except (asyncio.TimeoutError, aiohttp.ClientConnectorError) as e:
            logging.info(f"节点 {node['node_id']} 测试 ({node['ip']}) latency: -1ms, status: 离线")
            return (node['node_id'], node['ip'], -1, "离线")

    tasks = [test_single_node(node) for node in nodes]
    return await asyncio.gather(*tasks)

async def report_node_result(token, node_id, ip, latency, status):
    """报告单个节点的测试结果"""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    test_data = {
        "node_id": node_id,
        "ip": ip,
        "latency": latency,
        "status": status
    }
    
    async with aiohttp.ClientSession() as session:
        for _ in range(3):
            try:
                async with session.post(f"{BASE_URL}/test", headers=headers, json=test_data, timeout=5) as response:
                    if response.status == 200:
                        logging.info(f"节点 {node_id} 结果报告成功")
                        return
                    error_message = await response.text()
                    logging.error(f"报告节点 {node_id} 结果失败。状态: {response.status}, 错误信息: {error_message}")
            except Exception as e:
                logging.error(f"报告节点 {node_id} 结果时发生错误，正在重试: {e}")
                await asyncio.sleep(RETRY_DELAY)

async def report_all_node_results(token, results):
    """报告所有节点的测试结果"""
    for node_id, ip, latency, status in results:
        await report_node_result(token, node_id, ip, latency, status)

async def start_testing(token):
    """开始测试流程"""
    logging.info("正在测试节点...")
    async with aiohttp.ClientSession() as session:
        for _ in range(3):
            try:
                async with session.get(f"{BASE_URL}/nodes", headers={"Authorization": f"Bearer {token}"}, timeout=5) as response:
                    if response.status == 200:
                        nodes = await response.json()
                        results = await test_all_nodes(nodes)
                        await report_all_node_results(token, results)
                        return
                    error_message = await response.text()
                    logging.error(f"获取节点信息时发生错误。状态: {response.status}, 错误信息: {error_message}")
            except Exception as e:
                logging.error(f"获取节点信息时发生错误，正在重试: {e}")
                await asyncio.sleep(RETRY_DELAY)

async def main():
    print("""
*****************************************************
*           X:https://x.com/ferdie_jhovie           *
*           Tg:https://t.me/sdohuajia               *
*****************************************************
""")
    
    token = await load_token()
    if not token:
        logging.error("无法加载token。请确保token.txt文件存在且包含有效的token。")
        return

    logging.info("Token加载成功!")
    
    # 初始显示分数
    current_points = await fetch_points(token)
    if current_points is not None:
        print(f"{Colors.GREEN}当前分数: {current_points}{Colors.RESET}")
    
    next_heartbeat_time = datetime.now()
    next_test_time = datetime.now()
    
    while True:
        current_time = datetime.now()
        
        # 发送心跳
        if current_time >= next_heartbeat_time:
            await send_heartbeat(token, "用户名")  # 替换为实际用户名
            next_heartbeat_time = current_time + timedelta(seconds=HEARTBEAT_INTERVAL)
        
        # 测试节点
        if current_time >= next_test_time:
            await start_testing(token)
            current_points = await fetch_points(token)
            if current_points is not None:
                print(f"{Colors.GREEN}测试节点循环完成后当前分数: {current_points}{Colors.RESET}")
            next_test_time = current_time + timedelta(seconds=TEST_INTERVAL)
        
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
