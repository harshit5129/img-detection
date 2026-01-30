import discord
import asyncio
import logging
from typing import Dict, Optional
from config import ReactionType, DEFAULT_REACTIONS, guild_data, ReactionConfig

logger = logging.getLogger('DuplicateDetector')

class ReactionManager:
    def __init__(self):
        self.queues: Dict[int, asyncio.Queue] = {}
        self.tasks: Dict[int, asyncio.Task] = {}
    
    async def initialize_guild(self, guild_id: int):
        """Initialize reaction processing queue for a guild"""
        if guild_id not in self.queues:
            self.queues[guild_id] = asyncio.Queue()
            self.tasks[guild_id] = asyncio.create_task(
                self._process_queue(guild_id)
            )
    
    async def _process_queue(self, guild_id: int):
        """Background task to process reaction additions/removals"""
        queue = self.queues[guild_id]
        while True:
            try:
                action, message, emoji, timeout = await queue.get()
                
                if action == "add":
                    try:
                        await message.add_reaction(emoji)
                        if timeout > 0:
                            asyncio.create_task(
                                self._scheduled_remove(message, emoji, timeout)
                            )
                    except discord.Forbidden:
                        logger.warning(f"Cannot add reaction {emoji} in {message.guild.name}")
                    except Exception as e:
                        logger.error(f"Error adding reaction: {e}")
                
                elif action == "remove":
                    try:
                        await message.remove_reaction(emoji, message.guild.me)
                    except:
                        pass
                
                queue.task_done()
            except Exception as e:
                logger.error(f"Queue error: {e}")
                await asyncio.sleep(1)
    
    async def _scheduled_remove(self, message: discord.Message, emoji: str, timeout: int):
        """Schedule reaction removal"""
        await asyncio.sleep(timeout)
        try:
            msg = await message.channel.fetch_message(message.id)
            await msg.remove_reaction(emoji, msg.guild.me)
        except discord.NotFound:
            pass
        except Exception as e:
            logger.debug(f"Remove failed: {e}")
    
    async def add_reaction(self, message: discord.Message, reaction_type: ReactionType, 
                          custom_timeout: Optional[int] = None):
        """Add a reaction to a message"""
        if not message.guild:
            return
            
        await self.initialize_guild(message.guild.id)
        
        config = self._get_config(message.guild.id, reaction_type)
        if not config.enabled:
            return
        
        emoji = config.emoji
        timeout = custom_timeout if custom_timeout is not None else (
            config.remove_timeout if config.auto_remove else 0
        )
        
        await self.queues[message.guild.id].put(("add", message, emoji, timeout))
        return emoji
    
    async def remove_reaction(self, message: discord.Message, emoji: str):
        """Remove a specific reaction"""
        if message.guild and message.guild.id in self.queues:
            await self.queues[message.guild.id].put(("remove", message, emoji, 0))
    
    async def handle_sequence(self, message: discord.Message, status: str):
        """Handle reaction transitions"""
        try:
            if status == "processing":
                await self.add_reaction(message, ReactionType.PROCESSING)
            
            elif status == "unique":
                await self.remove_reaction(message, DEFAULT_REACTIONS[ReactionType.PROCESSING].emoji)
                await self.add_reaction(message, ReactionType.UNIQUE)
                
            elif status == "duplicate":
                await self.remove_reaction(message, DEFAULT_REACTIONS[ReactionType.PROCESSING].emoji)
                await self.add_reaction(message, ReactionType.DUPLICATE)
                
            elif status == "error":
                await self.remove_reaction(message, DEFAULT_REACTIONS[ReactionType.PROCESSING].emoji)
                await self.add_reaction(message, ReactionType.ERROR)
                
        except Exception as e:
            logger.error(f"Sequence error: {e}")
    
    def _get_config(self, guild_id: int, reaction_type: ReactionType) -> ReactionConfig:
        """Get reaction configuration"""
        reactions = guild_data.get(guild_id, {}).get('config', {}).get('auto_reactions', {})
        
        if reaction_type.value in reactions:
            data = reactions[reaction_type.value]
            if isinstance(data, dict):
                return ReactionConfig.from_dict(data)
            return data
        return DEFAULT_REACTIONS[reaction_type]
    
    def get_all_configs(self, guild_id: int) -> Dict[ReactionType, ReactionConfig]:
        """Get all reaction configurations"""
        configs = {}
        for r_type in ReactionType:
            configs[r_type] = self._get_config(guild_id, r_type)
        return configs

reaction_mgr = ReactionManager()
