"""Offline integration: existing project API delegates to user-global auth."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from modules import feishu_token

class SharedAuthTests(unittest.TestCase):
    def test_loads_current_disk_grant_and_updates_consumer_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'gfeishu.yaml'
            path.write_text('auth_dl-video:\n  user_access_token: newest\n  user_refresh_token: refresh\n'
                            f'  user_token_expire_time: {int(time.time())+7200}\n')
            credentials = {'feishu': {'user_access_token': 'stale'}, 'auth_feishuMSG-xls': {'user_access_token': 'other'}}
            session = Mock()
            with patch.object(feishu_token, 'SECRETS_FEISHU_PATH', path):
                self.assertEqual(feishu_token.ensure_feishu_token(credentials, session), 'newest')
            self.assertEqual(credentials['feishu']['user_access_token'], 'newest')
            self.assertEqual(credentials['auth_dl-video']['user_access_token'], 'newest')
            self.assertEqual(credentials['auth_feishuMSG-xls']['user_access_token'], 'other')
            session.post.assert_not_called()

if __name__ == '__main__': unittest.main()
