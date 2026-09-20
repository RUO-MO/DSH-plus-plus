# -*- coding: utf-8 -*-
"""P0-7 打包版模式解析：desktop 工程/补丁 + 供应商 llm-pi-ai（临时目录，不碰真实 config/文件）"""
import sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dsh_env, plugin_manager as P, session_store as ss

tmp = tempfile.mkdtemp()
home = os.path.join(tmp, 'dshhome')
os.makedirs(os.path.join(home, 'profiles', 'desktop'), exist_ok=True)

# --- 1) packaged 插件路径 ---
dsh_env.launch_mode = lambda: 'packaged'
dsh_env.dsh_home = lambda: (home, 'test')
assert P._desktop_project_dir() == os.path.join(home, 'profiles', 'desktop'), 'packaged 工程目录'
# packaged 补丁落点 = profile 用户层 cordis.patch.yml（desktop.cordis.yml 是包私有组合根，每次启动重置）
assert P._desktop_patch_path() == os.path.join(home, 'profiles', 'desktop', 'cordis.patch.yml'), 'packaged 补丁文件'
print('1) packaged 插件路径解析: PASS')

# --- 2) dev 模式回归：仍指向仓库 development/project/cordis.patch.yml ---
dsh_env.launch_mode = lambda: 'dev'
dsh_env.harness_root = lambda: os.path.join(tmp, 'hr')
assert P._desktop_project_dir() == os.path.join(tmp, 'hr', 'apps', 'desktop', '.desktop-build', 'development', 'project')
assert P._desktop_patch_path() == os.path.join(tmp, 'hr', 'apps', 'desktop', '.desktop-build', 'development', 'project', 'cordis.patch.yml')
print('2) dev 模式回归路径: PASS')

# --- 3) 空数组组合根写盘保留注释头（desktop.cordis.yml） ---
dsh_env.launch_mode = lambda: 'packaged'
patch = os.path.join(home, 'profiles', 'desktop', 'desktop.cordis.yml')
with open(patch, 'w', encoding='utf-8') as f:
    f.write('# Electron desktop composition root; package transactions own this file.\n[]\n')
rec = [{'name': 'demo', 'dir': tmp, 'entryRel': 'lib/index.js',
        'enabled': {'desktop': True}, 'targets': {'desktop': True}}]
os.makedirs(os.path.join(tmp, 'lib'), exist_ok=True)
with open(os.path.join(tmp, 'lib', 'index.js'), 'w', encoding='utf-8') as f:
    f.write('module.exports={}')
P._desktop_project_dir = lambda: os.path.join(home, 'profiles', 'desktop')
P._desktop_patch_path = lambda: patch
P._write_patch_file(patch, rec, 'desktop')
text = open(patch, encoding='utf-8').read()
assert text.startswith('# Electron'), '组合根注释头应保留'
assert P.BLOCK_BEGIN in text and 'dshskin-demo' in text
print('3) 组合根注释保留 + 托管块写入: PASS')

# --- 4) 供应商 llm-pi-ai 解析 ---
sfile = os.path.join(home, 'settings.yaml')
with open(sfile, 'w', encoding='utf-8') as f:
    f.write('''llm-pi-ai:
  providers:
    localqwen:
      displayName: Local Qwen3.8
      apiKeyEnv: LOCALQWEN_API_KEY
      api: openai-completions
      baseURL: http://127.0.0.1:8888/v1
      models:
        - id: qwen
          name: qwen
agent-default-model:
  provider: localqwen
  model: qwen
''')
ss.home = lambda cfg=None: home
info = ss.providers_info()
provs = info.get('providers', [])
assert len(provs) == 1, '应解析出 localqwen'
p0 = provs[0]
assert p0['id'] == 'localqwen' and p0['baseURL'] == 'http://127.0.0.1:8888/v1'
assert p0['api'] == 'openai-completions' and p0['apiKeyEnv'] == 'LOCALQWEN_API_KEY'
assert p0['apiKeyEnvSet'] is False and p0['models'] == 1
assert info['default_model'] == {'provider': 'localqwen', 'model': 'qwen'}
print('4) 供应商 llm-pi-ai 解析: PASS')

print('=== 打包版解析全部 PASS ===')
