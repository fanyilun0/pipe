import logging
import json
import aiohttp
import asyncio
from datetime import datetime, timedelta
import sys
import traceback
import random

# ANSI 转义序列
class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    RESET = "\033[0m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"

# 基础配置
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
BASE_URL = "https://pipe-network-backend.pipecanary.workers.dev/api"

# 时间间隔配置
HEARTBEAT_INTERVAL = 300  # 5分钟
TEST_INTERVAL = 15 * 60  # 30分钟
RETRY_DELAY = 5  # 重试延迟（秒）
REQUEST_DELAY_MIN = 10  # 请求最小延迟（秒）
REQUEST_DELAY_MAX = 30  # 请求最大延迟（秒）

# 在 Colors 类后面添加代理配置
PROXY_URL = "http://127.0.0.1:7890"

async def load_tokens():
    """从tokens.txt文件中加载多个token"""
    try:
        with open('token.txt', 'r') as file:
            tokens = [line.strip() for line in file.readlines() if line.strip()]
            return tokens
    except FileNotFoundError:
        logging.error("tokens.txt文件未找到")
    except Exception as e:
        logging.error(f"从tokens.txt文件加载token时发生错误: {e}")
    return []

async def get_ip():
    """获取当前IP地址"""
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        try:
            async with session.get("https://api64.ipify.org?format=json", 
                                 proxy=PROXY_URL, 
                                 timeout=5) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('ip')
        except Exception as e:
            logging.error(f"获取IP失败: {e}")
            await asyncio.sleep(RETRY_DELAY)
    return None

async def send_heartbeat(token):
    """发送心跳信号"""
    ip = await get_ip()
    if not ip:
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    data = {"ip": ip}
    
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        try:
            async with session.post(f"{BASE_URL}/heartbeat", 
                                  headers=headers, 
                                  json=data, 
                                  proxy=PROXY_URL,
                                  timeout=5) as response:
                if response.status == 200:
                    logging.info("心跳发送成功")
                elif response.status == 429:  # Rate limit error
                    return  # 静默处理限流错误
                else:
                    error_message = await response.text()
                    logging.error(f"发送心跳失败。状态: {response.status}, 错误信息: {error_message}")
        except Exception as e:
            logging.error(f"发送心跳时发生错误: {e}")
        finally:
            delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
            logging.info(f"等待 {delay:.1f} 秒后继续...")
            await asyncio.sleep(delay)

async def fetch_points(token):
    """获取当前分数"""
    headers = {"Authorization": f"Bearer {token}"}
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        try:
            async with session.get(
                f"{BASE_URL}/points", 
                headers=headers, 
                proxy=PROXY_URL,
                timeout=5
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('points')
                else:
                    logging.error(f"获取分数失败，状态码: {response.status}")
        except Exception as e:
            logging.error(f"获取分数时发生错误: {str(e)}")
        finally:
            delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
            logging.info(f"等待 {delay:.1f} 秒后继续...")
            await asyncio.sleep(delay)
    return None

async def test_all_nodes(nodes):
    """同时测试所有节点"""
    async def test_single_node(node):
        try:
            delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
            logging.info(f"等待 {delay:.1f} 秒后测试节点 {node['node_id']}...")
            await asyncio.sleep(delay)
            start = asyncio.get_event_loop().time()
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(f"http://{node['ip']}", 
                                     proxy=PROXY_URL,
                                     timeout=5) as node_response:
                    latency = (asyncio.get_event_loop().time() - start) * 1000
                    status = "在线" if node_response.status == 200 else "离线"
                    latency_value = latency if status == "在线" else -1
                    logging.info(f"节点 {node['node_id']} 测试 ({node['ip']}) latency: {latency_value:.2f}ms, status: {status}")
                    return (node['node_id'], node['ip'], latency_value, status)
        except (asyncio.TimeoutError, aiohttp.ClientConnectorError):
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
    
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        try:
            async with session.post(
                f"{BASE_URL}/test", 
                headers=headers, 
                json=test_data, 
                proxy=PROXY_URL,
                timeout=5
            ) as response:
                if response.status == 200:
                    logging.info(f"节点 {node_id} 结果报告成功")
                    return
                error_message = await response.text()
                logging.error(f"报告节点 {node_id} 结果失败。状态: {response.status}, 错误信息: {error_message}")
        except Exception as e:
            logging.error(f"报告节点 {node_id} 结果时发生错误: {str(e)}")
        finally:
            delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
            logging.info(f"等待 {delay:.1f} 秒后继续...")
            await asyncio.sleep(delay)

async def report_all_node_results(token, results):
    """报告所有节点的测试结果"""
    for node_id, ip, latency, status in results:
        await report_node_result(token, node_id, ip, latency, status)
        delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
        logging.info(f"等待 {delay:.1f} 秒后继续...")
        await asyncio.sleep(delay)

async def get_nodes(token):
    """获取节点列表"""
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        try:
            async with session.get(
                f"{BASE_URL}/nodes", 
                headers={"Authorization": f"Bearer {token}"},
                proxy=PROXY_URL,
                timeout=5
            ) as response:
                if response.status == 200:
                    return await response.json()
                error_message = await response.text()
                logging.error(f"获取节点信息失败。状态: {response.status}, 错误信息: {error_message}")
        except Exception as e:
            logging.error(f"获取节点信息时发生错误: {str(e)}")
        finally:
            delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
            logging.info(f"等待 {delay:.1f} 秒后继续...")
            await asyncio.sleep(delay)
    return None

async def run_single_token(token, shared_nodes=None):
    """为单个token运行节点测试"""
    logging.info(f"Token {token[:8]}... 开始运行")
    
    current_points = await fetch_points(token)
    if current_points is not None:
        print(f"{Colors.GREEN}Token {token[:8]}... 当前分数: {current_points}{Colors.RESET}")
    
    next_heartbeat_time = datetime.now()
    next_test_time = datetime.now()
    first_heartbeat = True
    
    try:
        while True:
            current_time = datetime.now()
            
            if current_time >= next_heartbeat_time:
                if first_heartbeat:
                    logging.info(f"Token {token[:8]}... 开始首次心跳...")
                    first_heartbeat = False
                await send_heartbeat(token)
                next_heartbeat_time = current_time + timedelta(seconds=HEARTBEAT_INTERVAL)
            
            if current_time >= next_test_time:
                if shared_nodes is None:
                    nodes = await get_nodes(token)
                else:
                    nodes = shared_nodes
                
                if nodes:
                    results = await test_all_nodes(nodes)
                    logging.info(f"节点测试结果: {json.dumps(results, indent=2)}")
                    await report_all_node_results(token, results)
                    current_points = await fetch_points(token)
                    if current_points is not None:
                        print(f"{Colors.GREEN}Token {token[:8]}... 测试完成后分数: {current_points}{Colors.RESET}")
                next_test_time = current_time + timedelta(seconds=TEST_INTERVAL)
            
            await asyncio.sleep(1)
    except Exception as e:
        logging.error(f"Token {token[:8]}... 运行出错: {e}")

async def run_node():
    """运行多个token的节测试"""
    tokens = await load_tokens()
    if not tokens:
        logging.error("无法加载tokens。请确保tokens.txt文件存在且包含有效的token。")
        return

    logging.info(f"成功加载 {len(tokens)} 个token!")
    
    # 获取共享的节点列表
    shared_nodes = await get_nodes(tokens[0])
    if not shared_nodes:
        logging.error("无法获取节点列表")
        return
    
    logging.info(f"成功获取节点列表: {json.dumps(shared_nodes, indent=2)}")
    
    # 为每个token创建一个任务，共享节点列表
    tasks = [run_single_token(token, shared_nodes) for token in tokens]
    
    try:
        # 并发运行所有token的任务
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        print("\n返回主菜单...")

async def save_registration_info(email, password, token, is_batch=False):
    """保存注册信息到文件"""
    try:
        # 保存详细注册信息
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        result_file = f'register_results_{timestamp}.txt' if not is_batch else f'batch_register_results_{timestamp}.txt'
        
        # 保存到结果文件
        with open(result_file, 'a', encoding='utf-8') as f:
            f.write(f"Email: {email}\n")
            f.write(f"Password: {password}\n")
            f.write(f"Token: {token}\n")
            f.write("-" * 50 + "\n")
        
        # 保存token到tokens.txt
        with open('tokens.txt', 'a', encoding='utf-8') as f:
            f.write(f"{token}\n")
            
        # 如果是批量注册，同时保存完整账号信息到新文件
        if is_batch:
            accounts_file = f'registered_accounts_{timestamp}.txt'
            with open(accounts_file, 'a', encoding='utf-8') as f:
                f.write(f"{email}:{password}:{token}\n")
        
        print(f"{Colors.GREEN}✓ 注册信息已保存到: {result_file}{Colors.RESET}")
        print(f"{Colors.GREEN}✓ Token已添加到: tokens.txt{Colors.RESET}")
        if is_batch:
            print(f"{Colors.GREEN}✓ 完整账号信息已保存到: {accounts_file}{Colors.RESET}")
        return True
    except Exception as e:
        print(f"{Colors.RED}✗ 保存注册信息时发生错误: {str(e)}{Colors.RESET}")
        return False

async def register_account():
    """注册单个账户"""
    print("\n=== 账户注册 ===")
    email = input("请输入邮箱: ")
    password = input("请输入密码: ")
    invite_code = input("请输入邀请码: ")
    
    async with aiohttp.ClientSession() as session:
        try:
            data = {
                "email": email,
                "password": password,
                "invite_code": invite_code
            }
            
            print("\n发送注册请求...")
            print(f"请求数据: {json.dumps(data, indent=2)}")
            
            async with session.post(
                f"{BASE_URL}/signup", 
                json=data, 
                proxy=PROXY_URL,
                timeout=10
            ) as response:
                response_text = await response.text()
                print(f"\n响应状态码: {response.status}")
                print(f"原始响应内容: {response_text}")
                
                if response.status in [200, 201]:
                    try:
                        if response_text.strip():
                            try:
                                print("\n正在解析响应...")
                                result = json.loads(response_text)
                                token = result.get('token')
                                print(f"解析的JSON结果: {json.dumps(result, indent=2)}")
                            except json.JSONDecodeError:
                                print("响应不是JSON格式，使用原始文本作为token")
                                token = response_text.strip()
                            
                            if token:
                                print(f"\n{Colors.GREEN}✓ 注册成功！{Colors.RESET}")
                                print(f"{Colors.CYAN}Token: {token}{Colors.RESET}")
                                # 询问是否保存
                                save = input("\n是否保存注册信息？(y/n): ")
                                if save.lower() == 'y':
                                    await save_registration_info(email, password, token)
                            else:
                                print(f"{Colors.RED}✗ 未能获取到有效的token{Colors.RESET}")
                    except Exception as e:
                        print(f"{Colors.RED}✗ 处理响应时发生错误: {str(e)}{Colors.RESET}")
                else:
                    print(f"{Colors.RED}✗ 注册失败！状态码: {response.status}{Colors.RESET}")
                    print(f"错误信息: {response_text}")
        except Exception as e:
            print(f"{Colors.RED}✗ 注册过程发生错误: {str(e)}{Colors.RESET}")

async def batch_register_accounts():
    """批量注册账户"""
    print("\n=== 批量账户注册 ===")
    
    try:
        with open('accounts.txt', 'r', encoding='utf-8') as f:
            accounts = [line.strip().split(':') for line in f if line.strip()]
    except FileNotFoundError:
        print(f"{Colors.RED}错误: accounts.txt 文件不存在{Colors.RESET}")
        return
    
    invite_code = input("请输入邀请码: ")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"\n开始注册 {len(accounts)} 个账号...")
    
    success_count = 0
    fail_count = 0
    
    async with aiohttp.ClientSession() as session:
        for idx, (email, password) in enumerate(accounts, 1):
            try:
                print(f"\n[{idx}/{len(accounts)}] 正在注册: {email}")
                data = {
                    "email": email.strip(),
                    "password": password.strip(),
                    "invite_code": invite_code
                }
                
                print(f"请求数据: {json.dumps(data, indent=2)}")
                
                async with session.post(
                    f"{BASE_URL}/signup",
                    json=data,
                    proxy=PROXY_URL,
                    timeout=10
                ) as response:
                    response_text = await response.text()
                    print(f"\n响应状态码: {response.status}")
                    print(f"原始响应内容: {response_text}")
                    
                    if response.status in [200, 201]:
                        try:
                            if response_text.strip():
                                try:
                                    print("\n正在解析响应...")
                                    result = json.loads(response_text)
                                    token = result.get('token')
                                    print(f"解析的JSON结果: {json.dumps(result, indent=2)}")
                                except json.JSONDecodeError:
                                    print("响应不是JSON格式，使用原始文本作为token")
                                    token = response_text.strip()
                                
                                if token:
                                    print(f"\n{Colors.GREEN}✓ 注册成功！{Colors.RESET}")
                                    print(f"{Colors.CYAN}Token: {token}{Colors.RESET}")
                                    await save_registration_info(email, password, token, is_batch=True)
                                    success_count += 1
                                else:
                                    print(f"{Colors.RED}✗ 未能获取到有效的token{Colors.RESET}")
                                    fail_count += 1
                        except Exception as e:
                            print(f"{Colors.RED}✗ 处理响应时发生错误: {str(e)}{Colors.RESET}")
                            fail_count += 1
                            
                    elif response.status == 429:
                        print(f"{Colors.RED}✗ 触发频率限制，等待60秒...{Colors.RESET}")
                        await asyncio.sleep(60)
                        idx -= 1
                        continue
                    else:
                        print(f"{Colors.RED}✗ 注册失败！状态码: {response.status}{Colors.RESET}")
                        print(f"错误信息: {response_text}")
                        fail_count += 1
                
                await asyncio.sleep(5)  # 注册间隔
                
            except Exception as e:
                print(f"{Colors.RED}✗ 注册过程发生错误: {str(e)}{Colors.RESET}")
                fail_count += 1
    
    print(f"\n批量注册完成！")
    print(f"成功: {success_count} 个账号")
    print(f"失败: {fail_count} 个账号")

async def batch_login_accounts():
    """批量登录账户并获取token"""
    print("\n=== 批量账户登录 ===")
    
    try:
        with open('accounts.txt', 'r', encoding='utf-8') as f:
            accounts = [line.strip().split(':') for line in f if line.strip()]
    except FileNotFoundError:
        print(f"{Colors.RED}错误: accounts.txt 文件不存在{Colors.RESET}")
        return
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    login_result_file = f'login_results_{timestamp}.txt'
    success_count = 0
    fail_count = 0
    
    print(f"\n开始登录 {len(accounts)} 个账号...")
    
    async with aiohttp.ClientSession() as session:
        for idx, (email, password) in enumerate(accounts, 1):
            try:
                print(f"\n[{idx}/{len(accounts)}] 正在登录: {email}")
                data = {
                    "email": email.strip(),
                    "password": password.strip()
                }
                
                print(f"请求数据: {json.dumps(data, indent=2)}")
                
                async with session.post(
                    "https://api.pipecdn.app/api/login",
                    json=data,
                    proxy=PROXY_URL,
                    timeout=10
                ) as response:
                    response_text = await response.text()
                    print(f"\n响应状态码: {response.status}")
                    print(f"原始响应内容: {response_text}")
                    
                    if response.status == 200:
                        try:
                            result = json.loads(response_text)
                            token = result.get('token')
                            if token:
                                print(f"{Colors.GREEN}✓ 登录成功！{Colors.RESET}")
                                print(f"{Colors.CYAN}Token: {token}{Colors.RESET}")
                                
                                # 保存登录结果
                                with open(login_result_file, 'a', encoding='utf-8') as f:
                                    f.write(f"Email: {email}\n")
                                    f.write(f"Password: {password}\n")
                                    f.write(f"Token: {token}\n")
                                    f.write("-" * 50 + "\n")
                                
                                # 保存token到tokens.txt
                                with open('tokens.txt', 'a', encoding='utf-8') as f:
                                    f.write(f"{token}\n")
                                
                                # 保存完整账号信息
                                with open(f'login_accounts_{timestamp}.txt', 'a', encoding='utf-8') as f:
                                    f.write(f"{email}:{password}:{token}\n")
                                
                                success_count += 1
                            else:
                                print(f"{Colors.RED}✗ 登录成功但未获取到token{Colors.RESET}")
                                fail_count += 1
                        except json.JSONDecodeError:
                            print(f"{Colors.RED}✗ 无效的JSON响应{Colors.RESET}")
                            fail_count += 1
                    
                    elif response.status == 429:
                        print(f"{Colors.RED}✗ 触发频率限制，等待60秒...{Colors.RESET}")
                        await asyncio.sleep(60)
                        idx -= 1  # 重试当前账号
                        continue
                    else:
                        print(f"{Colors.RED}✗ 登录失败！状态码: {response.status}{Colors.RESET}")
                        print(f"错误信息: {response_text}")
                        fail_count += 1
                        
                        # 记录失败信息
                        with open(login_result_file, 'a', encoding='utf-8') as f:
                            f.write(f"Email: {email} (登录失败)\n")
                            f.write(f"错误: {response_text}\n")
                            f.write("-" * 50 + "\n")
                
                await asyncio.sleep(2)  # 登录间隔
                
            except Exception as e:
                print(f"{Colors.RED}✗ 登录过程发生错误: {str(e)}{Colors.RESET}")
                fail_count += 1
                
                # 记录错误信息
                with open(login_result_file, 'a', encoding='utf-8') as f:
                    f.write(f"Email: {email} (发生错误)\n")
                    f.write(f"错误: {str(e)}\n")
                    f.write("-" * 50 + "\n")
    
    print(f"\n批量登录完成！")
    print(f"成功: {success_count} 个账号")
    print(f"失败: {fail_count} 个账号")
    print(f"\n所有登录信息已保存至:")
    print(f"1. login_results_{timestamp}.txt (详细日志)")
    print(f"2. login_accounts_{timestamp}.txt (完整账号信息)")
    print(f"3. tokens.txt (所有token)")

async def display_menu():
    """显示主菜单"""
    while True:
        print("\n" + "="*50)
        print(f"{Colors.CYAN}*X:https://x.com/ferdie_jhovie*")
        print(f"首发pipe network脚本，盗脚本可耻，请标注出处")
        print(f"*Tg:https://t.me/sdohuajia*{Colors.RESET}")
        print("="*50)
        print(f"\n{Colors.CYAN}请选择功能:{Colors.RESET}")
        print(f"{Colors.WHITE}1. 运行节点{Colors.RESET}")
        print(f"{Colors.WHITE}2. 注册单个账户{Colors.RESET}")
        print(f"{Colors.WHITE}3. 批量注册账户{Colors.RESET}")
        print(f"{Colors.WHITE}4. 批量登录账户{Colors.RESET}")  # 新选项
        print(f"{Colors.WHITE}5. 退出程序\n{Colors.RESET}")
        
        choice = input("请输入选项 (1-5): ")
        
        if choice == "1":
            await run_node()
        elif choice == "2":
            await register_account()
        elif choice == "3":
            await batch_register_accounts()
        elif choice == "4":
            await batch_login_accounts()
        elif choice == "5":
            print("\n感谢使用，再见！")
            sys.exit(0)
        else:
            print("\n无效选项，请重试")

async def main():
    print("""
*****************************************************
*           X:https://x.com/ferdie_jhovie           *
*           Tg:https://t.me/sdohuajia               *
*****************************************************
""")
    
    await display_menu()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n程序已退出")
        sys.exit(0)
