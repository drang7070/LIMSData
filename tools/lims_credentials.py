"""Configure LIMS credentials interactively without placing a password in shell history."""
import getpass
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.windows_credentials import write_dpapi_credential

if __name__ == '__main__':
    username = input('LIMS用户名: ').strip()
    password = getpass.getpass('LIMS密码（隐藏输入）: ')
    if not username or not password:
        raise SystemExit('用户名和密码不能为空')
    write_dpapi_credential(username, password, ROOT / 'data/.lims_credentials.dpapi')
    print('已加密保存至当前Windows用户的本地凭据文件。')
