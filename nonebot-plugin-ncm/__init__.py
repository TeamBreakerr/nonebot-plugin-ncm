#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from typing import Tuple, Any, Union, cast
from datetime import datetime
from pathlib import Path

from nonebot import on_regex, on_command, on_message
from nonebot.adapters.onebot.v11 import (Message, Bot,
                                         MessageSegment,
                                         GroupMessageEvent,
                                         PrivateMessageEvent)
from nonebot.log import logger
from nonebot.matcher import Matcher, current_bot
from nonebot.params import CommandArg, RegexGroup, Arg
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule
import httpx

from .config import Config
from .data_source import nncm, ncm_config, setting, Q, cmd, music

__plugin_meta__ = PluginMetadata(
    name="网易云无损音乐下载",
    description="基于go-cqhttp与nonebot2的 网易云无损音乐下载",
    usage=(
        '将网易云歌曲/歌单分享到群聊即可自动解析\n'
        '回复分享消息 + 文字`下载` 即可开始下载歌曲并上传到群文件(需要稍等一会)'
    ),
    config=Config,
    type="application",
    homepage="https://github.com/kitUIN/nonebot-plugin-ncm",
    supported_adapters={"~onebot.v11"},
)
# ========nonebot-plugin-ncm======
# ===========Constant=============
TRUE = ["True", "T", "true", "t"]
FALSE = ["False", "F", "false", "f"]
ADMIN = ["owner", "admin", "member"]


# ===============Rule=============
async def song_is_open(event: Union[GroupMessageEvent, PrivateMessageEvent]) -> bool:
    if isinstance(event, GroupMessageEvent):
        info = setting.search(Q["group_id"] == event.group_id)
        if info:
            return info[0]["song"]
        else:
            setting.insert({"group_id": event.group_id, "song": False, "list": False})
            return False
    elif isinstance(event, PrivateMessageEvent):
        info = setting.search(Q["user_id"] == event.user_id)
        if info:
            return info[0]["song"]
        else:
            setting.insert({"user_id": event.user_id, "song": True, "list": True})
            return True


async def playlist_is_open(event: Union[GroupMessageEvent, PrivateMessageEvent]) -> bool:
    if isinstance(event, GroupMessageEvent):
        info = setting.search(Q["group_id"] == event.group_id)
        if info:
            return info[0]["list"]
        else:
            setting.insert({"group_id": event.group_id, "song": False, "list": False})
            return False
    elif isinstance(event, PrivateMessageEvent):
        info = setting.search(Q["user_id"] == event.user_id)
        if info:
            return info[0]["list"]
        else:
            setting.insert({"user_id": event.user_id, "song": True, "list": True})
            return True

async def check_search(event: Union[GroupMessageEvent, PrivateMessageEvent]) -> bool:
    try:
        info = setting.search(Q["global"] == "search")
        if info:
            return info[0]["value"]
        else:
            setting.insert({"global": "search", "value": True})
            return True
    except Exception:
        return False

async def music_set_rule(event: Union[GroupMessageEvent, PrivateMessageEvent]) -> bool:
    # 权限设置
    return event.sender.role in ADMIN[:ncm_config.ncm_admin_level] or event.get_user_id() in ncm_config.superusers

async def music_reply_rule(event: Union[GroupMessageEvent, PrivateMessageEvent]) -> bool:
    try:
        # logger.info(event.get_plaintext())
        return bool(event.reply and event.get_plaintext().strip() == "下载")
    except Exception:
        return False

# ============Matcher=============
ncm_set = on_command("ncm",
                     rule=Rule(music_set_rule),
                     priority=1, block=False)
'''功能设置'''
music_regex = on_regex("(song|url)\?id=([0-9]+)(|&)",
                       priority=2, block=False)
'''歌曲id识别'''
playlist_regex = on_regex("playlist\?id=([0-9]+)&",
                          priority=2, block=False)
'''歌单识别'''
music_reply = on_message(priority=2,
                         rule=Rule(music_reply_rule),
                         block=False)
'''回复下载'''
search = on_command("点歌",
                    rule=Rule(check_search),
                    priority=2, block=False)
'''点歌'''


@search.handle()
async def search_receive(matcher: Matcher, args: Message = CommandArg()):
    if args:
        matcher.set_arg("song", args)  # 如果用户发送了参数则直接赋值


@search.got("song", prompt="要点什么歌捏?")
async def receive_song(bot: Bot,
                      event: Union[GroupMessageEvent, PrivateMessageEvent],
                      song: Message = Arg(),
                      ):
    keyword = song.extract_plain_text()
    logger.info(f"收到点歌请求，关键词: {keyword}")
    
    _id = await nncm.search_song(keyword=keyword, limit=1)
    logger.info(f"搜索到歌曲ID: {_id}")
    
    # 先发送网易云卡片
    message_id = await bot.send(event=event, message=Message(MessageSegment.music(type_="163", id_=_id)))
    nncm.get_song(message_id=message_id["message_id"], nid=_id)

    audio_content = None  # 用于存储音频内容

    try:
        # 检查缓存
        info = music.search(Q["id"] == _id)
        if info:
            logger.info(f"找到缓存文件: {info[0]['file']}")
            try:
                with open(info[0]["file"], "rb") as f:
                    audio_content = f.read()
                logger.info("成功读取缓存文件")
            except FileNotFoundError:
                logger.warning(f"缓存文件不存在，将重新下载: {info[0]['file']}")

        if not audio_content:  # 如果没有从缓存获取到内容
            # 没有缓存，下载新文件
            logger.info("开始获取歌曲详情")
            data = nncm.get_detail([_id])[0]
            logger.debug(f"歌曲详情: {data}")
            
            if data["code"] == 404:
                logger.error("未从网易云读取到下载地址")
                return
                
            url = data["url"]
            logger.info(f"获取到下载地址: {url}")
            
            # 直接使用 httpx 下载文件到指定目录
            async with httpx.AsyncClient() as client:
                logger.info("开始下载音频文件")
                response = await client.get(url)
                if response.status_code == 200:
                    # 确保音乐目录存在
                    music_dir = Path("music")
                    music_dir.mkdir(exist_ok=True)
                    
                    # 使用音乐ID作为文件名
                    file_path = music_dir / f"{_id}.{data['type']}"
                    logger.info(f"将文件保存到: {file_path}")
                    
                    # 保存文件
                    file_path.write_bytes(response.content)
                    logger.info(f"文件下载成功，大小: {len(response.content)} bytes")
                    
                    audio_content = response.content
                    
                    # 保存到缓存
                    cf = {
                        "id": int(_id),
                        "file": str(file_path),
                        "filename": f"{data['ncm_name']}.{data['type']}",
                        "from": "song",
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                    music.insert(cf)
                    logger.info("成功保存到缓存数据库")
                else:
                    logger.error(f"下载音频失败，状态码: {response.status_code}")
                    return
            
    except Exception as e:
        logger.error(f"处理音频时发生错误: {repr(e)}")
        logger.exception(e)
        return

    # 如果成功获取到音频内容，发送语音消息
    if audio_content:
        await search.finish(MessageSegment.record(audio_content))


@music_regex.handle()
async def music_receive(bot: Bot, event: Union[GroupMessageEvent, PrivateMessageEvent],
                        regroup: Tuple[Any, ...] = RegexGroup()):
    nid = regroup[1]
    logger.info(f"已识别NID:{nid}的歌曲")

    nncm.get_song(nid=nid, message_id=event.message_id)


@playlist_regex.handle()
async def music_list_receive(bot: Bot, event: Union[GroupMessageEvent, PrivateMessageEvent],
                             regroup: Tuple[Any, ...] = RegexGroup()):
    lid = regroup[0]
    logger.info(f"已识别LID:{lid}的歌单")
    nncm.get_playlist(lid=lid, message_id=event.message_id)


@music_reply.handle()
async def music_reply_receive(bot: Bot, event: Union[GroupMessageEvent, PrivateMessageEvent]):
    info = nncm.check_message(int(event.dict()["reply"]["message_id"]))
    if info is None:
        return
    if info["type"] == "song" and await song_is_open(event):
        await bot.send(event=event, message="少女祈祷中🙏...上传时间较久,请勿重复发送命令")
        await nncm.music_check(info["nid"], event)
    elif info["type"] == "playlist" and await playlist_is_open(event):
        await bot.send(event=event, message=info["lmsg"] + "\n下载中,上传时间较久,请勿重复发送命令")
        await nncm.music_check(info["ids"], event, info["lid"])


@ncm_set.handle()
async def set_receive(bot: Bot, event: Union[GroupMessageEvent, PrivateMessageEvent],
                      args: Message = CommandArg()):  # 功能设置接收
    logger.debug(f"权限为{event.sender.role}的用户<{event.sender.nickname}>尝试使用命令{cmd}ncm {args}")
    if args:
        args = str(args).split()
        if len(args) == 1:
            mold = args[0]
            if isinstance(event, GroupMessageEvent):
                info = setting.search(Q["group_id"] == event.group_id)
                # logger.info(info)
                if info:
                    if mold in TRUE:
                        info[0]["song"] = True
                        info[0]["list"] = True
                        setting.update(info[0], Q["group_id"] == event.group_id)
                        msg = "已开启自动下载功能"
                        await bot.send(event=event, message=Message(MessageSegment.text(msg)))
                    elif mold in FALSE:
                        info[0]["song"] = False
                        info[0]["list"] = False
                        setting.update(info[0], Q["group_id"] == event.group_id)
                        msg = "已关闭自动下载功能"
                        await bot.send(event=event, message=Message(MessageSegment.text(msg)))
                    logger.debug(f"用户<{event.sender.nickname}>执行操作成功")
                else:
                    if mold in TRUE:
                        setting.insert({"group_id": event.group_id, "song": True, "list": True})
                    elif mold in FALSE:
                        setting.insert({"group_id": event.group_id, "song": False, "list": False})
            elif isinstance(event, PrivateMessageEvent):
                info = setting.search(Q["user_id"] == event.user_id)
                # logger.info(info)
                if info:
                    if mold in TRUE:
                        info[0]["song"] = True
                        info[0]["list"] = True
                        setting.update(info[0], Q["user_id"] == event.user_id)
                        msg = "已开启下载功能"
                        await bot.send(event=event, message=Message(MessageSegment.text(msg)))
                    elif mold in FALSE:
                        info[0]["song"] = False
                        info[0]["list"] = False
                        setting.update(info[0], Q["user_id"] == event.user_id)
                        msg = "已关闭下载功能"
                        await bot.send(event=event, message=Message(MessageSegment.text(msg)))
                    logger.debug(f"用户<{event.sender.nickname}>执行操作成功")
                else:
                    if mold in TRUE:
                        setting.insert({"user_id": event.user_id, "song": True, "list": True})
                    elif mold in FALSE:
                        setting.insert({"user_id": event.user_id, "song": False, "list": False})
        elif len(args) == 2 and args[0] == "search":
            mold = args[1]
            info = setting.search(Q["global"] == "search")
            if info:
                if mold in TRUE:
                    info[0]["value"] = True
                    setting.update(info[0], Q["global"] == "search")
                    msg = "已开启点歌功能"
                    await bot.send(event=event, message=Message(MessageSegment.text(msg)))
                elif mold in FALSE:
                    info[0]["value"] = False
                    setting.update(info[0], Q["global"] == "search")
                    msg = "已关闭点歌功能"
                    await bot.send(event=event, message=Message(MessageSegment.text(msg)))
                logger.debug(f"用户<{event.sender.nickname}>执行操作成功")
            else:
                if mold in TRUE:
                    setting.insert({"global": "search", "value": True})
                elif mold in FALSE:
                    setting.insert({"global": "search", "value": False})
        elif len(args) == 3 and args[0] == "private":
            qq = args[1]
            mold = args[2]
            info = setting.search(Q["user_id"] == qq)
            # logger.info(info)
            if info:
                if mold in TRUE:
                    info[0]["song"] = True
                    info[0]["list"] = True
                    setting.update(info[0], Q["user_id"] == qq)
                    msg = f"已开启用户{qq}的下载功能"
                    await bot.send(event=event, message=Message(MessageSegment.text(msg)))
                elif mold in FALSE:
                    info[0]["song"] = False
                    info[0]["list"] = False
                    setting.update(info[0], Q["user_id"] == qq)
                    msg = f"已关闭用户{qq}的下载功能"
                    await bot.send(event=event, message=Message(MessageSegment.text(msg)))
                logger.debug(f"用户<{event.sender.nickname}>执行操作成功")
            else:
                if mold in TRUE:
                    setting.insert({"user_id": event.user_id, "song": True, "list": True})
                elif mold in FALSE:
                    setting.insert({"user_id": event.user_id, "song": False, "list": False})
    else:
        msg = f"{cmd}ncm:获取命令菜单\r\n说明:网易云歌曲分享到群内后回复机器人即可下载\r\n" \
              f"{cmd}ncm t:开启解析\r\n{cmd}ncm f:关闭解析\n{cmd}点歌 歌名:点歌"
        return await ncm_set.finish(message=MessageSegment.text(msg))
