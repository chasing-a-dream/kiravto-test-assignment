import os
from dataclasses import dataclass
from pathlib import Path

TEST_CHANNEL='@autostock_demo_test'
PRODUCTION_CHANNEL='@autostock_demo_main'

def delivery_channel(mode,enabled,test_channel=TEST_CHANNEL,production_channel=PRODUCTION_CHANNEL):
    if mode=='test' and test_channel.casefold()==TEST_CHANNEL:return TEST_CHANNEL
    if mode=='production' and enabled is True and production_channel.casefold()==PRODUCTION_CHANNEL:return PRODUCTION_CHANNEL
    raise RuntimeError('Delivery mode/channel is invalid or production was not explicitly enabled by the human')

@dataclass(frozen=True)
class Settings:
    db: str = 'data/stock.sqlite'
    api_token: str = ''
    bot_token: str = ''
    admin_chat: str = ''
    moderators: tuple = ()
    channel: str = '@autostock_demo_test'
    posts_enabled: bool = False
    llm_key: str = ''
    llm_model: str = 'openrouter/free'
    llm_daily_budget: str = '0.10'
    telegram_proxy: str = ''
    mode: str = 'test'
    production_enabled: bool = False

    def __post_init__(self):
        expected=delivery_channel(self.mode,self.production_enabled)
        if self.channel.casefold()!=expected:
            raise RuntimeError('Channel does not match explicitly configured delivery mode')

    @classmethod
    def load(cls):
        # Runtime only. Agents must never inspect this file or print this object.
        from dotenv import load_dotenv
        load_dotenv()
        mode = os.getenv('TELEGRAM_MODE','test')
        enabled=os.getenv('PRODUCTION_ENABLED','false').lower()=='true'
        db = os.getenv('DB_PATH','data/stock.sqlite')
        channel=delivery_channel(mode,enabled,os.getenv('TELEGRAM_TEST_CHANNEL',TEST_CHANNEL),os.getenv('TELEGRAM_PRODUCTION_CHANNEL',PRODUCTION_CHANNEL))
        Path(db).parent.mkdir(parents=True,exist_ok=True)
        return cls(db,os.getenv('API_TOKEN',''),os.getenv('TELEGRAM_BOT_TOKEN',''),os.getenv('TELEGRAM_ADMIN_CHAT_ID',''),tuple(x.strip() for x in os.getenv('TELEGRAM_MODERATOR_IDS','').split(',') if x.strip()),channel,os.getenv('POST_JOBS_ENABLED','false').lower()=='true',os.getenv('LLM_API_KEY','') if os.getenv('LLM_ENABLED','false').lower()=='true' else '',os.getenv('LLM_MODEL','openrouter/free'),os.getenv('LLM_DAILY_BUDGET_USD','0.10'),os.getenv('TELEGRAM_PROXY_URL',''),mode,enabled)
