#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from typing import Tuple, Any, Union, cast, Optional
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
from nonebot_plugin_alconna.builtins.uniseg.music_share import (
    MusicShare,
    MusicShareKind,
)
from nonebot_plugin_alconna.uniseg import UniMessage

from .config import Config
from .data_source import nncm, ncm_config, setting, Q, cmd, music

# Constants
SONG_TIP = "\n使用指令 `direct` 获取播放链接"

class SongInfo:
    def __init__(self, song_id: int, name: str, artists: list, url: str, audio_url: str, cover_url: str):
        self.song_id = song_id
        self.display_name = name
        self.display_artists = ",".join(artist["name"] for artist in artists)
        self.url = url
        self.playable_url = audio_url
        self.cover_url = cover_url

    @classmethod
    async def from_song_id(cls, song_id: int) -> "SongInfo":
        # Get song details from API
        song_detail = nncm.api.track.GetTrackDetail(song_ids=[song_id])["songs"][0]
        audio_info = nncm.api.track.GetTrackAudio(song_ids=[song_id], bitrate=ncm_config.ncm_bitrate * 1000)["data"][0]
        
        return cls(
            song_id=song_id,
            name=song_detail["name"],
            artists=song_detail["ar"],
            url=f"https://music.163.com/#/song?id={song_id}",
            audio_url=audio_info["url"],
            cover_url=song_detail["al"]["picUrl"]
        )

    async def get_description(self) -> str:
        return f"{self.display_name} - {self.display_artists}"

async def sign_music_card(info: SongInfo) -> str:
    """Sign the music card with the signing service if configured"""
    if not ncm_config.ncm_card_sign_url:
        raise ValueError("Card signing URL not configured")
        
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=ncm_config.ncm_card_sign_timeout,
    ) as cli:
        body = {
            "type": "custom",
            "url": info.url,
            "audio": info.playable_url,
            "title": info.display_name,
            "image": info.cover_url,
            "singer": info.display_artists,
        }
        return (
            (await cli.post(ncm_config.ncm_card_sign_url, json=body))
            .raise_for_status()
            .text
        )

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
ncm_set = on_command("ncm", rule=Rule(music_set_rule), priority=1, block=True)
music_regex = on_regex("(song|url)\?id=([0-9]+)(|&)", priority=2, block=True)
playlist_regex = on_regex("playlist\?id=([0-9]+)&", priority=2, block=True)
music_reply = on_message(rule=Rule(music_reply_rule), priority=2, block=True)
search = on_command("点歌", rule=Rule(check_search), priority=2, block=True)

@search.handle()
async def search_receive(matcher: Matcher, args: Message = CommandArg()):
    if args:
        matcher.set_arg("song", args)  # 如果用户发送了参数则直接赋值

async def construct_info_msg(song_info: SongInfo, tip_command: bool = True) -> UniMessage:
    """Construct an info message for the song"""
    tip = SONG_TIP if tip_command else ""
    desc = await song_info.get_description()
    return UniMessage.image(url=song_info.cover_url) + f"{desc}\n{song_info.url}{tip}"

async def send_song_card_msg(song_info: SongInfo):
    """Send song as a card message"""
    if ncm_config.ncm_card_sign_url:
        return await UniMessage.hyper("json", await sign_music_card(song_info)).send(
            fallback=False,
        )
    
    return await UniMessage(
        MusicShare(
            kind=MusicShareKind.NeteaseCloudMusic,
            title=song_info.display_name,
            content=song_info.display_artists,
            url=song_info.url,
            thumbnail=song_info.cover_url,
            audio=song_info.playable_url,
            summary=song_info.display_artists,
        ),
    ).send(fallback=False)

async def send_song(song_info: SongInfo, event):
    """Send song with fallback options"""
    receipt = None
    
    if ncm_config.ncm_send_as_card:
        try:
            receipt = await send_song_card_msg(song_info)
        except Exception as e:
            logger.warning(f"Failed to send song card: {e}")
    
    if not receipt:
        receipt = await construct_info_msg(song_info).send(event=event)
    
    return receipt

@search.got("song", prompt="要点什么歌捏?")
async def receive_song(bot: Bot,
                      event: Union[GroupMessageEvent, PrivateMessageEvent],
                      song: Message = Arg(),
                      ):
    keyword = song.extract_plain_text()
    logger.info(f"收到点歌请求，关键词: {keyword}")
    
    _id = await nncm.search_song(keyword=keyword, limit=1)
    if not _id:
        await search.finish("没有找到这首歌呢")
    logger.info(f"搜索到歌曲ID: {_id}")
    
    try:
        # Get song info and create custom card
        song_info = await SongInfo.from_song_id(_id)
        
        # Send song with fallback options
        receipt = await send_song(song_info, event)
        
        # Get the message id from the receipt
        try:
            # Try to get message_id from receipt's message_id attribute
            message_id = receipt.message_id
        except AttributeError:
            try:
                # Try to get message_id from receipt's data attribute
                message_id = receipt.data["message_id"]
            except (AttributeError, KeyError):
                # If both attempts fail, use event's message_id as fallback
                message_id = event.message_id
                logger.warning("Could not get message_id from receipt, using event message_id as fallback")
        
        # Store song info in cache
        nncm.get_song(message_id=message_id, nid=_id)

        # Only proceed with media download if enabled
        if not ncm_config.ncm_send_media:
            return

        audio_content = None  # 用于存储音频内容

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

    # 如果成功获取到音频内容，先发送封面，再发送语音消息
    if audio_content:
        try:
            # 发送歌曲封面
            async with httpx.AsyncClient() as client:
                cover_response = await client.get(song_info.cover_url)
                if cover_response.status_code == 200:
                    await bot.send(event=event, message=MessageSegment.image(cover_response.content))
            # 发送语音消息
            await bot.send(event=event, message=MessageSegment.record(file=audio_content))
        except Exception as e:
            logger.error(f"发送消息失败: {e}")
            # 如果发送失败，尝试上传文件
            if 'data' in locals() and 'file_path' in locals():
                await nncm.upload_data_file(event=event, data={
                    "file": str(file_path),
                    "filename": f"{data['ncm_name']}.{data['type']}"
                })
            else:
                logger.error("无法上传文件：缺少必要的文件信息")

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
        await ncm_set.finish(message=MessageSegment.text(msg))
