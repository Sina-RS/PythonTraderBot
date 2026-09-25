#!/usr/bin/env python
__author__ = "Alireza Sadabadi"
__copyright__ = "Copyright (c) 2026 Alireza Sadabadi. All rights reserved."
__credits__ = ["Alireza Sadabadi"]
__license__ = "Apache"
__version__ = "2.0"
__maintainer__ = "Alireza Sadabadi"
__email__ = "alirezasadabady@gmail.com"
__status__ = "Test"
__doc__ = "you can see the tutorials in https://youtube.com/@alirezasadabadi?si=d8o7LK_Ai1Hf68is"

import json
import os
import time
from urllib.parse import quote
import requests

token = '8977275651:AAGRk_pnhlTFC6K5KLV1sl__AW01di69B8w'
chatId = '-1004488069207'
ip = '127.0.0.1'
port = '10808'
useProxy = True

# Telegram rejects messages longer than 4096 characters.
MAX_MSG_LEN = 4096
# Error returned when a basic group was upgraded to a supergroup.
MIGRATED_DESC = 'group chat was upgraded to a supergroup chat'
# How many times a call is retried on transient network/proxy failures.
MAX_ATTEMPTS = 3
REQUEST_TIMEOUT = 30  # seconds - never let a send hang the trading loop

# Once the group migrates, remember the new chat id next to this module so the
# next run starts with the correct id instead of failing on every message.
_CHAT_ID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             'telegram_chat_id.txt')


class TeleBot():

    def __init__(self):
        self.url = f'https://api.telegram.org/bot{token}/'
        self.proxies = {'https':f'http://{ip}:{port}'}
        self.chatId = self._load_chat_id()
        # Adopt a migrated chat id up front, before the first real send.
        self._probe_chat()

    # ------------------------------------------------------------------
    # chat id persistence / migration
    # ------------------------------------------------------------------
    def _load_chat_id(self):
        try:
            if os.path.exists(_CHAT_ID_FILE):
                with open(_CHAT_ID_FILE, 'r', encoding='utf-8') as fh:
                    saved = fh.read().strip()
                if saved:
                    return saved
        except BaseException as e:
            print(f"TelegramBot: could not read saved chat id: {e}")
        return chatId

    def _save_chat_id(self, new_id):
        try:
            with open(_CHAT_ID_FILE, 'w', encoding='utf-8') as fh:
                fh.write(str(new_id))
        except BaseException as e:
            print(f"TelegramBot: could not save new chat id: {e}")

    def _adopt_migration(self, check):
        """Telegram returns parameters.migrate_to_chat_id with the 400 error."""
        params = check.get('parameters') or {}
        new_id = params.get('migrate_to_chat_id')
        if not new_id:
            return False
        new_id = str(new_id)
        if new_id != str(self.chatId):
            print(f"TelegramBot: group migrated to supergroup - chat id "
                  f"{self.chatId} -> {new_id}")
            self.chatId = new_id
            self._save_chat_id(new_id)
        return True

    def _probe_chat(self):
        """Ask getChat about the stored id; adopt a new id if it migrated."""
        try:
            check = self._api('getChat')
        except BaseException as e:
            print(f"TelegramBot: chat probe failed: {e}")
            return
        if check.get('ok'):
            return
        if MIGRATED_DESC in str(check.get('description', '')):
            if not self._adopt_migration(check):
                print(f"TelegramBot: chat probe: {check.get('description')} "
                      f"(no migrate_to_chat_id returned - will retry on send)")

    # ------------------------------------------------------------------
    # transport
    # ------------------------------------------------------------------
    def _api(self, cmd, **params):
        """Call a Bot API method, retrying transient network/proxy failures."""
        params.setdefault('chat_id', self.chatId)
        last_error = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                if useProxy:
                    resp = requests.get(self.url + cmd, params=params,
                                        proxies=self.proxies,
                                        timeout=REQUEST_TIMEOUT)
                else:
                    resp = requests.get(self.url + cmd, params=params,
                                        timeout=REQUEST_TIMEOUT)
                return resp.json()
            except BaseException as e:
                last_error = e
                if attempt < MAX_ATTEMPTS:
                    time.sleep(attempt)  # 1s, 2s backoff
        raise RuntimeError(f"Telegram API {cmd} failed after "
                           f"{MAX_ATTEMPTS} attempts: {last_error}")

    @staticmethod
    def _chunks(text, size=MAX_MSG_LEN):
        if not text:
            return ['']
        return [text[i:i + size] for i in range(0, len(text), size)]

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def DecodeMsg(resp):
        decoded=''
        for line in resp:
            decoded+=line.decode('utf-8')
        return decoded

    def SendMessage(self, message='empty'):
        """Send a message, following a group->supergroup migration once."""
        for chunk in self._chunks(str(message)):
            self._send_chunk(chunk)
        return True

    def _send_chunk(self, text, allow_migration=True):
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                check = self._api('sendMessage', text=text)
            except BaseException as e:
                print(f"An exception has occurred in TelegramBot SendMessage: {str(e)}")
                if attempt < MAX_ATTEMPTS:
                    time.sleep(attempt)
                continue

            if check.get('ok'):
                print('Message Send Successfully')
                return True

            description = str(check.get('description', ''))
            if allow_migration and MIGRATED_DESC in description:
                if self._adopt_migration(check):
                    # resend once against the new supergroup id
                    return self._send_chunk(text, allow_migration=False)
            # Permanent failure (bad request) - report it instead of looping.
            print(description)
            return False
        return False

    def SendPhoto(self, photo, caption='', allow_migration=True):
        cmd = 'sendPhoto'
        data = {'chat_id': self.chatId, 'caption': str(caption)}
        files = {'photo': ('trend.png', photo, 'image/png')}
        proxies = self.proxies if useProxy else None
        last_error = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = requests.post(self.url + cmd, data=data, files=files,
                                     proxies=proxies, timeout=REQUEST_TIMEOUT)
                check = resp.json()
            except BaseException as e:
                last_error = e
                if attempt < MAX_ATTEMPTS:
                    time.sleep(attempt)
                continue

            if check.get('ok'):
                print('Photo Send Successfully')
                return True

            description = str(check.get('description', ''))
            if allow_migration and MIGRATED_DESC in description:
                if self._adopt_migration(check):
                    data['chat_id'] = self.chatId
                    return self.SendPhoto(photo, caption, allow_migration=False)
            print(description)
            return False
        print(f"An exception has occurred in TelegramBot SendPhoto: {last_error}")
        return False
