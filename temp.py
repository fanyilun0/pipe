import logging
import json
import aiohttp
import asyncio
from datetime import datetime, timedelta
import sys
import traceback
import random
import time

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
TEST_INTERVAL = 6 * 60  # 5分钟
RETRY_DELAY = 5  # 重试延迟（秒）
REQUEST_DELAY_MIN = 15  # 请求最小延迟（秒）
REQUEST_DELAY_MAX = 30  # 请求最大延迟（秒）

# 在全局配置部分添加代理控制变量
USE_PROXY = False  # 设置为 False 则全局禁用代理
PROXY_URL = "http://127.0.0.1:7890" if USE_PROXY else None

# 添加到全局配置部分
IP_BLACKLIST = [
    "123.45.67.89",
    "98.76.54.32"
]

# 添加到全局配置部分
MAX_CONCURRENT_TOKENS = 10  # 同时运行的token数量
MAX_CONCURRENT_REQUESTS = 1  # 同时运行的请求数量

# 添加到全局配置部分（在文件开头的配置区域）
TIMEOUT = 10  # 全局超时设置（秒）

# 添加到全局配置部分
MAX_RETRIES = 3  # 最大重试次数
RETRY_BACKOFF = 5  # 重试基础延迟时间（秒）

# 添加到全局配置部分
class RateLimiter:
    def __init__(self, rate, per):
        self.rate = rate  # 令牌生成速率
        self.per = per    # 时间周期（秒）
        self.tokens = rate  # 当前可用令牌数
        self.last_update = time.time()
        self.lock = asyncio.Lock()

    async def acquire(self):
        async with self.lock:
            now = time.time()
            time_passed = now - self.last_update
            self.tokens = min(self.rate, self.tokens + time_passed * (self.rate / self.per))
            self.last_update = now

            if self.tokens < 1:
                wait_time = (1 - self.tokens) * (self.per / self.rate)
                logging.info(f"限流等待 {wait_time:.1f} 秒...")
                await asyncio.sleep(wait_time)
                self.tokens = 1

            self.tokens -= 1
            return True

# 创建全局限流器实例（5次/分钟）
REQUEST_LIMITER = RateLimiter(rate=6, per=60)

async def send_request_with_rate_limit(session, method, url, **kwargs):
    """带限流控制的请求发送函数"""
    await REQUEST_LIMITER.acquire()
    return await getattr(session, method)(url, **kwargs)

async def send_request_with_retry(session, method, url, max_retries=MAX_RETRIES, **kwargs):
    """带重试机制的请求发送函数"""
    # 如果全局禁用代理，则移除代理参数
    if not USE_PROXY and 'proxy' in kwargs:
        del kwargs['proxy']
        
    for attempt in range(max_retries):
        try:
            await REQUEST_LIMITER.acquire()
            async with await getattr(session, method)(url, **kwargs) as response:
                return response
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            if attempt == max_retries - 1:  # 最后一次尝试
                raise
            
            delay = RETRY_BACKOFF * (2 ** attempt) + random.uniform(0, 1)
            logging.info(f"请求失败 (尝试 {attempt + 1}/{max_retries}), {delay:.1f} 秒后重试: {str(e)}")
            await asyncio.sleep(delay)

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
    print(f"发送心跳: {data}")
    
    connector = aiohttp.TCPConnector(ssl=False, force_close=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        try:
            response = await send_request_with_retry(
                session,
                'post',
                f"{BASE_URL}/heartbeat",
                headers=headers,
                json=data,
                proxy=PROXY_URL,
                timeout=TIMEOUT
            )
            
            if response.status == 200:
                logging.info("心跳发送成功")
            elif response.status == 429:
                return
            else:
                error_message = await response.text()
                logging.error(f"发送心跳失败。状态: {response.status}, 错误信息: {error_message}")
        except Exception as e:
            logging.error(f"发送心跳时发生错误: {e}")
            await asyncio.sleep(RETRY_DELAY)

async def get_ip():
    """获取当前IP地址"""
    connector = aiohttp.TCPConnector(ssl=False, force_close=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        try:
            response = await send_request_with_retry(
                session,
                'get',
                "https://api64.ipify.org?format=json",
                proxy=PROXY_URL,
                timeout=TIMEOUT
            )
            
            if response.status == 200:
                data = await response.json()
                return data.get('ip')
        except Exception as e:
            logging.error(f"获取IP失败: {e}")
            await asyncio.sleep(RETRY_DELAY)
    return None

async def fetch_points(token, token_info=""):
    """获取当前分数"""
    headers = {"Authorization": f"Bearer {token}"}
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        try:
            async with await send_request_with_rate_limit(
                session,
                'get',
                f"{BASE_URL}/points",
                headers=headers,
                proxy=PROXY_URL,
                timeout=TIMEOUT
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('points')
                else:
                    logging.error(f"{token_info} 获取分数失败，状态码: {response.status}")
        except Exception as e:
            logging.error(f"{token_info} 获取分数时发生错误: {str(e)}")
        return None

async def test_all_nodes(nodes, token_info=""):
    """同时测试所有节点"""
    async def test_single_node(node, session, token_info=""):
        try:
            delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
            logging.info(f"{token_info} 等待 {delay:.1f} 秒后测试节点 {node['node_id']}...")
            await asyncio.sleep(delay)
            
            start = asyncio.get_event_loop().time()
            async with await send_request_with_rate_limit(
                session,
                'get',
                f"http://{node['ip']}",
                proxy=PROXY_URL,
                timeout=TIMEOUT
            ) as node_response:
                latency = (asyncio.get_event_loop().time() - start) * 1000
                status = "在线" if node_response.status == 200 else "离线"
                latency_value = latency if status == "在线" else -1
                logging.info(f"{token_info} 节点 {node['node_id']} 测试 ({node['ip']}) latency: {latency_value:.2f}ms, status: {status}")
                return (node['node_id'], node['ip'], latency_value, status)
        except Exception:
            logging.info(f"{token_info} 节点 {node['node_id']} 测试 ({node['ip']}) latency: -1ms, status: 离线")
            return (node['node_id'], node['ip'], -1, "离线")

    # 创建所有节点的测试任务，每次最多测试 5 个节点
    results = []
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        for i in range(0, len(nodes), 5):
            batch = nodes[i:i+5]
            tasks = [test_single_node(node, session, token_info) for node in batch]
            batch_results = await asyncio.gather(*tasks)
            results.extend(batch_results)
            
            # 每批次测试后添加随机延迟
            if i + 5 < len(nodes):  # 如果不是最后一批
                delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
                logging.info(f"{token_info} 完成一批节点测试，等待 {delay:.1f} 秒后继续...")
                await asyncio.sleep(delay)
    
    return results

async def report_node_result(token, node_id, ip, latency, status, token_info=""):
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
            async with await send_request_with_rate_limit(
                session,
                'post',
                f"{BASE_URL}/test",
                headers=headers,
                json=test_data,
                proxy=PROXY_URL,
                timeout=TIMEOUT
            ) as response:
                response_text = await response.text()
                print(f"{token_info} 报告节点 {node_id} 结果响应: {response_text}")

                if response.status == 200:
                    logging.info(f"{token_info} 节点 {node_id} 结果报告成功")
                    return
                error_message = await response.text()
                logging.error(f"{token_info} 报告节点 {node_id} 结果失败。状态: {response.status}, 错误信息: {error_message}")
        except Exception as e:
            error_details = {
                "error_type": type(e).__name__,
                "error_message": str(e),
                "traceback": traceback.format_exc()
            }
            logging.error(f"{token_info} 报告节点 {node_id} 结果时发生错误: \n"
                        f"错误类型: {error_details['error_type']}\n"
                        f"错误信息: {error_details['error_message']}\n"
                        f"详细追踪:\n{error_details['traceback']}")

async def report_all_node_results(token, results, token_info=""):
    """报告所有节点的测试结果"""
    for node_id, ip, latency, status in results:
        # 在发送请求前先等待
        delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX) + 30
        logging.info(f"{token_info} 等待 {delay:.1f} 秒后发送节点 {node_id} 的结果...")
        await asyncio.sleep(delay)
        
        # 最多重试3次
        for attempt in range(3):
            try:
                await report_node_result(token, node_id, ip, latency, status, token_info)
                break  # 成功则退出重试循环
            except Exception as e:
                error_details = {
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "traceback": traceback.format_exc()
                }
                if "429" in str(e) and attempt < 2:  # 如果是速率限制且不是最后一次尝试
                    retry_delay = 60 + random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)  # 触发限流后等待更长时间
                    logging.info(f"{token_info} 触发速率限制，等待 {retry_delay:.1f} 秒后重试...")
                    await asyncio.sleep(retry_delay)
                else:
                    logging.error(f"{token_info} 报告节点 {node_id} 结果失败 (尝试 {attempt + 1}/3): \n"
                                f"错误类型: {error_details['error_type']}\n"
                                f"错误信息: {error_details['error_message']}\n"
                                f"详细追踪:\n{error_details['traceback']}")
                    break

async def get_nodes(token):
    """获取节点列表"""
    for attempt in range(MAX_RETRIES):
        try:
            connector = aiohttp.TCPConnector(
                ssl=False, 
                force_close=True,
                enable_cleanup_closed=True,
                ttl_dns_cache=300
            )
            timeout = aiohttp.ClientTimeout(total=TIMEOUT, connect=10)
            
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                async with await send_request_with_retry(
                    session,
                    'get',
                    f"{BASE_URL}/nodes",
                    headers={"Authorization": f"Bearer {token}"},
                    proxy=PROXY_URL,
                    timeout=timeout
                ) as response:
                    if response.status == 200:
                        nodes = await response.json()
                        filtered_nodes = [node for node in nodes if node['ip'] not in IP_BLACKLIST]
                        if len(nodes) != len(filtered_nodes):
                            logging.info(f"已过滤 {len(nodes) - len(filtered_nodes)} 个黑名单IP节点")
                        return filtered_nodes
                    error_message = await response.text()
                    logging.error(f"获取节点信息失败。状态: {response.status}, 错误信息: {error_message}")
                    
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            if attempt == MAX_RETRIES - 1:  # 最后一次尝试
                logging.error(f"获取节点信息时发生错误: {str(e)}")
                return None
            
            # 添加更详细的错误信息输出
            error_type = type(e).__name__
            error_details = str(e)
            if isinstance(e, aiohttp.ClientConnectorError):
                error_details = f"连接错误: {e.host}:{e.port} - {str(e)}"
            elif isinstance(e, asyncio.TimeoutError):
                error_details = "请求超时"
            
            # 计算指数退避延迟
            delay = RETRY_BACKOFF * (2 ** attempt) + random.uniform(1, 5)
            logging.error(f"获取节点列表失败 (尝试 {attempt + 1}/{MAX_RETRIES})")
            logging.error(f"错误类型: {error_type}")
            logging.error(f"错误详情: {error_details}")
            logging.info(f"{delay:.1f} 秒后重试...")
            await asyncio.sleep(delay)
            
        except Exception as e:
            error_type = type(e).__name__
            error_details = str(e)
            logging.error(f"获取节点信息时发生未知错误:")
            logging.error(f"错误类型: {error_type}")
            logging.error(f"错误详情: {error_details}")
            logging.error(f"错误追踪:\n{traceback.format_exc()}")
            return None
            
        finally:
            delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
            logging.info(f"等待 {delay:.1f} 秒后继续...")
            await asyncio.sleep(delay)
    
    return None

async def run_single_token(token, token_index, shared_nodes=None):
    """为单个token运行节点测试"""
    token_info = f"[Token {token_index}] {token[-8:]}..."
    logging.info(f"{token_info} 开始运行")
    
    # 初始化上次分数
    last_points = await fetch_points(token)
    if last_points is not None:
        print(f"{Colors.GREEN}{token_info} 当前分数: {last_points}{Colors.RESET}")
    
    next_heartbeat_time = datetime.now()
    next_test_time = datetime.now()
    first_heartbeat = True
    
    try:
        while True:
            current_time = datetime.now()
            
            if current_time >= next_heartbeat_time:
                if first_heartbeat:
                    logging.info(f"{token_info} 开始首次心跳...")
                    first_heartbeat = False
                await send_heartbeat(token)
                next_heartbeat_time = current_time + timedelta(seconds=HEARTBEAT_INTERVAL)
            
            if current_time >= next_test_time:
                if shared_nodes is None:
                    nodes = await get_nodes(token)
                else:
                    nodes = shared_nodes
                
                if nodes:
                    results = await test_all_nodes(nodes, token_info)
                    logging.info(f"{token_info} 节点测试结果: {json.dumps(results, separators=(',', ':'))}")
                    await report_all_node_results(token, results, token_info)
                    current_points = await fetch_points(token)
                    if current_points is not None:
                        points_diff = current_points - last_points if last_points is not None else 0
                        diff_str = f"(+{points_diff})" if points_diff > 0 else f"({points_diff})" if points_diff < 0 else "(+0)"
                        print(f"{Colors.GREEN}{token_info} 测试完成后分数: {current_points} {diff_str}{Colors.RESET}")
                        last_points = current_points
                next_test_time = current_time + timedelta(seconds=TEST_INTERVAL)
            
            await asyncio.sleep(1)
    except Exception as e:
        logging.error(f"{token_info} 运行出错: {e}")

async def run_token_with_semaphore(token, idx, shared_nodes):
    """运行单个token的任务"""
    token_info = f"[Token {idx}] {token[-8:]}..."
    try:
        logging.info(f"{token_info} 开始运行")
        retry_count = 0
        max_retries = 3
        
        while retry_count < max_retries:
            try:
                await run_single_token(token, idx, shared_nodes)
                break
            except Exception as e:
                retry_count += 1
                if retry_count < max_retries:
                    retry_delay = 30 * retry_count
                    logging.error(f"{token_info} 运行出错 (重试 {retry_count}/{max_retries}): {str(e)}")
                    await asyncio.sleep(retry_delay)
                else:
                    logging.error(f"{token_info} 达到最大重试次数，放弃执行")
                    raise
                    
    except Exception as e:
        logging.error(f"{token_info} 严重错误: {str(e)}\n{traceback.format_exc()}")
    finally:
        logging.info(f"{token_info} 任务结束")

async def run_node():
    """运行多个token的节点测试"""
    tokens = await load_tokens()
    if not tokens:
        logging.error("无法加载tokens。请确保tokens.txt文件存在且包含有效的token。")
        return

    logging.info(f"成功加载 {len(tokens)} 个token!")
    
    # 持续尝试获取节点列表直到成功
    shared_nodes = None
    attempt = 0
    while shared_nodes is None:
        attempt += 1
        try:
            shared_nodes = await get_nodes(tokens[0])
            if shared_nodes:
                logging.info(f"成功获取节点列表: {len(shared_nodes)} 个节点")
                break
                
            # 如果获取失败，使用指数退避策略
            wait_time = min(300, RETRY_BACKOFF * (2 ** (attempt - 1)) + random.uniform(1, 5))  # 最大等待5分钟
            logging.error(f"第 {attempt} 次尝试获取节点列表失败，{wait_time:.1f} 秒后重试...")
            await asyncio.sleep(wait_time)
            
        except Exception as e:
            wait_time = min(300, RETRY_BACKOFF * (2 ** (attempt - 1)) + random.uniform(1, 5))
            logging.error(f"获取节点列表时发生错误: {str(e)}")
            logging.error(f"第 {attempt} 次尝试失败，{wait_time:.1f} 秒后重试...")
            await asyncio.sleep(wait_time)
            continue
    
    # 节点列表获取成功后，继续执行后续操作
    logging.info("节点列表获取成功，开始执行任务...")
    
    # 创建所有token的任务
    tasks = []
    for idx, token in enumerate(tokens, 1):
        task = asyncio.create_task(run_token_with_semaphore(token, idx, shared_nodes))
        tasks.append(task)
    
    try:
        # 等待所有任务完成或出错
        done, pending = await asyncio.wait(
            tasks,
            return_when=asyncio.FIRST_EXCEPTION
        )
        
        # 检查是否有任务出错
        for task in done:
            try:
                await task
            except Exception as e:
                logging.error(f"Token任务出错: {str(e)}\n{traceback.format_exc()}")
        
        # 取消所有未完成的任务
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            
    except KeyboardInterrupt:
        logging.info("收到中断信号，正在停止所有任务...")
        # 取消所有任务
        for task in tasks:
            task.cancel()
        # 等待所有任务完成取消
        await asyncio.gather(*tasks, return_exceptions=True)
        logging.info("所有任务已停止")
    except Exception as e:
        logging.error(f"运行出错: {str(e)}\n{traceback.format_exc()}")
    finally:
        logging.info("节点测试完成")

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
            
            async with await send_request_with_rate_limit(
                session,
                'post',
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
                # 添加随机延迟
                delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
                print(f"\n等待 {delay:.1f} 秒后续...")
                await asyncio.sleep(delay)
                
                print(f"\n[{idx}/{len(accounts)}] 正在注册: {email}")
                data = {
                    "email": email.strip(),
                    "password": password.strip(),
                    "invite_code": invite_code
                }
                
                print(f"请求数据: {json.dumps(data, indent=2)}")
                
                async with await send_request_with_rate_limit(
                    session,
                    'post',
                    f"{BASE_URL}/signup",
                    json=data,
                    proxy=PROXY_URL,
                    timeout=10
                ) as response:
                    response_text = await response.text()
                    print(f"\n响应状态码: {response.status}")
                    print(f"原始响应内容: {response_text}")
                    
                    if response.status in [200, 201]:
                        print(f"{Colors.GREEN}✓ 注册成功！{Colors.RESET}")
                        # 保存注册成的账号息
                        with open(f'registered_accounts_{timestamp}.txt', 'a', encoding='utf-8') as f:
                            f.write(f"{email}:{password}\n")
                        success_count += 1
                    elif response.status == 429:
                        print(f"{Colors.RED}✗ 触发频率限制，等待60秒...{Colors.RESET}")
                        await asyncio.sleep(60)
                        idx -= 1  # 重试当前账号
                        continue
                    else:
                        print(f"{Colors.RED}✗ 注册失败！状态码: {response.status}{Colors.RESET}")
                        print(f"错误信息: {response_text}")
                        fail_count += 1
                
            except Exception as e:
                print(f"{Colors.RED}✗ 注册过程发生错误: {str(e)}{Colors.RESET}")
                fail_count += 1
    
    print(f"\n批量注册完成！")
    print(f"成功: {success_count} 个账号")
    print(f"失败: {fail_count} 个账号")
    print(f"\n注册信息已保存至: registered_accounts_{timestamp}.txt")

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
    success_count = 0
    fail_count = 0
    
    print(f"\n开始登录 {len(accounts)} 个账号...")
    
    async with aiohttp.ClientSession() as session:
        for idx, (email, password) in enumerate(accounts, 1):
            try:
                # 添加随机延迟
                delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
                print(f"\n等待 {delay:.1f} 秒后继续...")
                await asyncio.sleep(delay)
                
                print(f"\n[{idx}/{len(accounts)}] 正在登录: {email}")
                data = {
                    "email": email.strip(),
                    "password": password.strip()
                }
                
                print(f"请求数据: {json.dumps(data, indent=2)}")
                
                async with await send_request_with_rate_limit(
                    session,
                    'post',
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
                
            except Exception as e:
                print(f"{Colors.RED}✗ 登录过程发生错误: {str(e)}{Colors.RESET}")
                fail_count += 1
    
    print(f"\n批量登录完成！")
    print(f"成功: {success_count} 个账号")
    print(f"失败: {fail_count} 个账号")
    print(f"\n登录信息已保存至:")
    print(f"1. login_accounts_{timestamp}.txt (完整账号信息)")
    print(f"2. tokens.txt (所有token)")

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
        print(f"{Colors.WHITE}2. 册单个账户{Colors.RESET}")
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
