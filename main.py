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

async def load_token():
    """从token.txt文件中加载token"""
    try:
        with open('token.txt', 'r') as file:
            token = file.read().strip()
            return token
    except FileNotFoundError:
        logging.error("token.txt文件未找到")
        return None
    except Exception as e:
        logging.error(f"从token.txt文件加载token时发生错误: {e}")
        return None

async def send_heartbeat(token, username):
    """发送心跳"""
    try:
        # 获取IP地址
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api64.ipify.org?format=json") as ip_response:
                if ip_response.status == 200:
                    ip_data = await ip_response.json()
                    ip = ip_data.get('ip')
                    logging.info(f"IP地址: {ip}")
                else:
                    logging.error(f"获取IP失败。状态: {ip_response.status}")

        # 发送心跳
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        data = {
            "username": username,
            "ip": ip,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{BASE_URL}/heartbeat", headers=headers, json=data) as response:
                if response.status == 200:
                    logging.info("心跳发送成功")
                else:
                    logging.error(f"发送心跳失败。状态: {response.status}")
    except aiohttp.ClientResponseError as e:
        logging.error(f"发送心跳时发生错误: {e}")

async def fetch_points(token):
    """获取分数"""
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{BASE_URL}/points", headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    points = data.get('points', None)
                    return points
                else:
                    logging.error(f"获取分数时发生错误。状态: {response.status}")
                    return None
    except aiohttp.ClientResponseError as e:
        logging.error(f"获取分数时发生错误: {e}")
        return None

async def start_testing(token):
    """开始测试节点"""
    logging.info("正在测试节点...")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{BASE_URL}/nodes", headers={"Authorization": f"Bearer {token}"}) as response:
                if response.status == 200:
                    nodes = await response.json()
                    for node in nodes:
                        await test_node(token, node)
                else:
                    logging.error(f"获取节点信息时发生错误。状态: {response.status}")
    except aiohttp.ClientResponseError as e:
        logging.error(f"获取节点信息时发生错误: {e}")

async def test_node(token, node):
    """测试单个节点"""
    try:
        start = asyncio.get_event_loop().time()
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://{node['ip']}", timeout=10) as node_response:
                latency = asyncio.get_event_loop().time() - start
                if node_response.status == 200:
                    logging.info(f"节点 {node['node_id']} 测试 ({node['ip']}) latency: {latency:.6f}ms")
                    logging.info(f"报告节点结果 {node['node_id']}.")
                    
                    headers = {
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    }
                    test_data = {
                        "node_id": node['node_id'],
                        "latency": latency,
                        "status": "online"
                    }
                    async with session.post(f"{BASE_URL}/test", headers=headers, json=test_data) as test_response:
                        if test_response.status != 200:
                            logging.error(f"Error")
                else:
                    logging.info(f"节点 {node['node_id']} 测试 ({node['ip']}) latency: -1ms")
                    logging.info(f"报告节点结果 {node['node_id']}.")
                    logging.error(f"节点 {node['ip']} 离线")
    except (asyncio.TimeoutError, aiohttp.ClientConnectorError) as e:
        logging.info(f"节点 {node['node_id']} 测试 ({node['ip']}) latency: -1ms")
        logging.info(f"报告节点结果 {node['node_id']}.")
        logging.error(f"测试节点 {node['ip']} 时发生错误: {e}")

async def main():
    print("""
*****************************************************
*           X:https://x.com/ferdie_jhovie         *
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
                    logging.error(f"获取分数失败")
                next_test_time = current_time + timedelta(seconds=TEST_INTERVAL)
            
            # 等待下一个事件触发
            await asyncio.sleep(1)
    else:
        logging.error("无法加载token。请确保token.txt文件存在且包含有效的token。")

if __name__ == "__main__":
    asyncio.run(main())
