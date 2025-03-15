from typing import Optional

import nonebot
from pydantic import BaseModel, Extra


# ============Config=============
class Config(BaseModel, extra=Extra.ignore):
    superusers: list = []

    ncm_admin_level: int = 1
    '''设置命令权限(1:仅限superusers和群主,2:在1的基础上管理员,3:所有用户)'''

    ncm_phone: Optional[int] = None
    '''手机号'''

    ncm_ctcode: int = 86
    '''手机号区域码,默认86'''

    ncm_password: Optional[str] = None
    '''密码'''
    ncm_bitrate: int = 320
    '''下载码率(单位K) 96及以下为m4a,320及以上为flac,中间mp3'''

    ncm_card_sign_url: Optional[str] = None
    '''音乐卡片签名服务URL'''

    ncm_card_sign_timeout: int = 10
    '''音乐卡片签名超时时间(秒)'''

    ncm_send_as_card: bool = True
    '''是否以卡片形式发送音乐'''

    ncm_send_media: bool = True
    '''是否发送媒体文件'''


global_config = nonebot.get_driver().config
ncm_config = Config(**global_config.dict())  # 载入配置
