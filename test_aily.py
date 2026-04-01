import logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
from services.ai_service import _get_tenant_access_token, _call_aily

print('=== 测试1: 获取 Token ===')
token = _get_tenant_access_token()
if token:
    print(f'Token OK: {token[:30]}...')
else:
    print('Token 获取失败!')
    exit(1)

print()
print('=== 测试2: 调用 Aily ===')
result = _call_aily('你好，请问飞书如何创建群聊？')
print(f'返回内容长度: {len(result)}')
print(f'内容: {result[:500]}')
