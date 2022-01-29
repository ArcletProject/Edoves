from typing import Dict, TYPE_CHECKING, List, Type, Optional

from ...utilles import IOStatus
from .monomers import MiraiMonomer
from ..medium import Message, Notice, Request
from .chain import MessageChain
from ...utilles.data_source_info import DataSourceInfo
from ...main.protocol import NetworkProtocol

if TYPE_CHECKING:
    from ...main.monomer import Monomer
    from .server_docker import MAHServerDocker


class MAHProtocol(NetworkProtocol):
    storage: Dict[Type["MAHServerDocker"], "MAHServerDocker"]

    async def medium_transport(self, action: str):
        server = list(self.storage.values())[-1]
        medium = await self.scene.monomer_protocol.get_medium()
        if server.metadata.state in (IOStatus.CLOSED, IOStatus.CLOSE_WAIT):
            return
        if action.startswith("message"):
            target = medium.get("target")
            if action.endswith("send"):
                sender = self.scene.monomer_protocol.storage.get(target)
                rest = medium.get('rest')
                source_type = rest.get('type')
                if source_type:
                    if source_type.startswith("Friend") and sender.compare("Friend"):
                        action = "sendFriendMessage"
                    elif source_type.startswith("Group") and sender.compare("Member"):
                        if sender.parents:
                            target = sender.metadata.group_id
                            action = "sendGroupMessage"
                else:
                    if sender.prime_tag == "Member":
                        if sender.parents:
                            target = sender.metadata.group_id
                            action = "sendGroupMessage"
                    elif sender.prime_tag == "Friend":
                        action = "sendFriendMessage"
                resp = await server.behavior.session_handle(
                    "POST",
                    action,
                    {
                        "sessionKey": server.metadata.session_key,
                        "target": target,
                        "messageChain": medium.get("content").dict()["__root__"],
                        **(
                            {"quote": rest.get("quote")} if medium.get("reply") else {}
                        )

                    }
                )
                self.scene.edoves.logger.info(
                    f"{self.scene.edoves.self.metadata.identifier}: "
                    f"{action.replace('send', '').replace('Message', '')}({target})"
                    f" <- {medium.get('content').to_text()}"
                )
                return resp
            if action.endswith("revoke"):
                return await server.behavior.session_handle(
                    "POST",
                    "recall",
                    {
                        "sessionKey": server.metadata.session_key,
                        "target": target,
                    }
                )
        if action.startswith('nudge'):
            rest = medium.get('rest')
            source_type = rest.get('type')
            subject = target = medium.get("target")
            sender = self.scene.monomer_protocol.storage.get(target)
            kind = sender.prime_tag
            if source_type:
                if source_type.startswith("Friend") and sender.compare("Friend"):
                    kind = "Friend"
                elif source_type.startswith("Group") and sender.compare("Member"):
                    if sender.parents:
                        subject = sender.metadata.group_id
                        kind = "Group"
            else:
                if sender.prime_tag == "Member":
                    if sender.parents:
                        subject = sender.metadata.group_id
                        kind = "Group"
            await server.behavior.session_handle(
                "POST",
                "sendNudge",
                {
                    "sessionKey": server.metadata.session_key,
                    "target": target,
                    "subject": subject,
                    "kind": kind
                }
            )
            self.scene.edoves.logger.info(
                f"{self.scene.edoves.self.metadata.identifier}: "
                f"{kind}({target}) <- Nudge"
            )
        if action in ('accept', 'reject'):
            rtype = medium.get('type')
            await server.behavior.session_handle(
                "POST"
                f"resp/{rtype[0].lower() + rtype[1:]}",
                {
                    "sessionKey": server.metadata.session_key,
                    "eventId": medium.get('eventId'),
                    "fromId": medium['content'].get('fromId'),
                    "groupId": medium['content'].get('groupId'),
                    "operate": medium.get('operate'),
                    "message": medium.get('msg')
                }
            )

    async def parse_raw_data(self):
        data: Dict = await self.get_medium()
        ev_type: str = data.pop("type")
        server = list(self.storage.values())[-1]
        if not ev_type:
            return
        if ev_type.endswith("Message"):
            sender_data: Dict = data.get("sender")
            if ev_type.startswith("Friend"):
                sender = self.include_friend(sender_data)
            elif ev_type.startswith("Group") or ev_type.startswith("Temp"):
                sender = self.include_member(sender_data)
                group = self.include_group(sender_data.get("group"))
                if sender.metadata.identifier not in group.children:
                    group.set_child(sender)
                sender.metadata.update_data("group_id", group.metadata.identifier)
            else:
                if not (sender := self.scene.monomers.get(str(sender_data.get('id')))):
                    sender = MiraiMonomer(
                        self.scene.monomer_protocol,
                        sender_data.get("nickname"),
                        str(sender_data.get('id')),
                        sender_data.get("remark")
                    )
                    sender.set_parent(self.scene.edoves.self)
                    self.scene.monomers.setdefault(sender.metadata.identifier, sender)
            await self.post_message(
                "MessageReceived",
                sender,
                ev_type,
                data.get("messageChain")
            )
        elif ev_type.startswith('Nudge'):
            operator_id = str(data.pop('fromId'))
            target_id = str(data.pop('target'))
            operator = self.scene.monomers.get(operator_id)
            subject = data.pop('subject')
            if subject['kind'] == "Group":
                if not operator or not getattr(operator.metadata, "group_id"):
                    info = await server.behavior.session_handle(
                        "GET",
                        "memberInfo",
                        {"sessionKey": server.metadata.session_key, "target": subject['id'], "memberId": operator_id}
                    )
                    operator = self.include_member(info)
                    group = self.include_group(info.get("group"))
                    if operator.metadata.identifier not in group.children:
                        group.set_child(operator)
                    operator.metadata.update_data("group_id", group.metadata.identifier)
                else:
                    operator.set_prime_tag("Member")
            elif subject['kind'] == "Friend":
                if not operator:
                    profile = await server.behavior.session_handle(
                        "GET",
                        "friendProfile",
                        {"sessionKey": server.metadata.session_key, "target": operator_id}
                    )
                    profile.setdefault("id", operator_id)
                    operator = self.include_friend(profile)
                else:
                    operator.set_prime_tag("Friend")
            target = self.scene.monomers.get(target_id) or target_id
            await self.post_notice(
                "NoticeMe",
                operator,
                ev_type,
                {**data, "target": target}
            )
        elif ev_type.startswith("Bot"):
            if ev_type == "BotGroupPermissionChangeEvent":
                group = self.include_group(data.pop('group'))
                self.scene.edoves.self.metadata.update_data("group_id", group.metadata.identifier)
                await self.post_notice(
                    "MonomerStatusUpdate",
                    self.scene.edoves.self,
                    ev_type,
                    data
                )
            elif ev_type in ("BotMuteEvent", "BotUnmuteEvent"):
                operator_data = data.pop('operator')
                operator = self.include_member(operator_data)
                group = self.include_group(operator_data['group'])
                if operator.metadata.identifier not in group.children:
                    group.set_child(operator)
                operator.metadata.update_data("group_id", group.metadata.identifier)
                await self.post_notice(
                    "MonomerStatusUpdate",
                    self.scene.edoves.self,
                    ev_type,
                    data,
                    operator=operator,
                    action="set" + ev_type.replace('Bot', '').replace('Event', '')
                )
            elif ev_type == "BotJoinGroupEvent":
                group = self.include_group(data.pop('group'))
                self.scene.edoves.self.metadata.update_data("group_id", group.metadata.identifier)
                await self.post_notice(
                    "RelationshipSetup",
                    self.scene.edoves.self,
                    ev_type,
                    data,
                    relationship="Group"
                )
            elif ev_type == "BotLeaveEventActive":
                group = self.exclude_group(data.pop('group'))
                await self.post_notice(
                    "RelationshipTerminate",
                    self.scene.edoves.self,
                    ev_type,
                    {"group": group},
                    relationship="Group"
                )
            elif ev_type == "BotLeaveEventKick":
                group = self.exclude_group(data.pop('group'))
                operator = self.exclude_member(data.pop('operator'), group.metadata.identifier)
                operator.metadata.update_data("group_id", group.metadata.identifier)
                await self.post_notice(
                    "RelationshipSevered",
                    self.scene.edoves.self,
                    ev_type,
                    {"group": group},
                    operator=operator,
                    relationship="Group"
                )
            else:
                await self.post_notice(
                    "NoticeMe",
                    self.scene.edoves.self,
                    ev_type,
                    {}
                )
        elif ev_type.startswith("Friend"):
            if ev_type != "FriendRecallEvent":
                friend = self.include_friend(data.pop('friend'))
                await self.post_notice(
                    "MonomerMetadataUpdate",
                    friend,
                    ev_type,
                    data
                )
            else:
                if not (operator := self.scene.monomers.get(str(data.pop('operator')))):
                    return
                message_id = data.pop('messageId')
                message = await server.behavior.session_handle(
                    "GET",
                    "messageFromId",
                    {"sessionKey": server.metadata.session_key, "id": message_id}
                )
                await self.post_message(
                    "MessageRevoke",
                    operator,
                    ev_type,
                    message['messageChain']
                )
        elif ev_type.startswith('Group'):
            group_data = data.pop('group')
            group = self.include_group(group_data)
            if ev_type == "GroupAllowConfessTalkEvent":
                await self.post_notice(
                    "MonomerStatusUpdate",
                    group,
                    ev_type,
                    data,
                    action="AllowConfessTalk"
                )
            else:
                operator_data = data.pop('operator')
                operator = self.include_member(operator_data)
                if operator.metadata.identifier not in group.children:
                    group.set_child(group)
                operator.metadata.update_data("group_id", group.metadata.identifier)
                if ev_type.endswith('RecallEvent'):
                    message_id = data.pop('messageId')
                    message = await server.behavior.session_handle(
                        "GET",
                        "messageFromId",
                        {"sessionKey": server.metadata.session_key, "id": message_id}
                    )
                    await self.post_message(
                        "MessageRevoke",
                        operator,
                        ev_type,
                        message['messageChain']
                    )
                elif ev_type in (
                        'GroupNameChangeEvent',
                        "GroupEntranceAnnouncementChangeEvent",
                ):
                    await self.post_notice(
                        "MonomerMetadataUpdate",
                        group,
                        ev_type,
                        data,
                        operator=operator
                    )
                else:
                    await self.post_notice(
                        "MonomerStatusUpdate",
                        group,
                        ev_type,
                        data,
                        operator=operator
                    )
        elif ev_type.startswith('Member'):
            member_data = data.pop('member')
            group_data = member_data.pop('group')
            group = self.include_group(group_data)
            if 'MemberLeaveEvent' in ev_type:
                member = self.exclude_member(member_data, group_data.get("id"))
                if ev_type.endswith('Quit'):
                    await self.post_notice(
                        "RelationshipTerminate",
                        member,
                        ev_type,
                        {"group": group},
                        relationship="Member"
                    )
                elif ev_type.endswith('Kick'):
                    operator_data = data.pop('operator')
                    operator = self.include_member(operator_data)
                    if operator.metadata.identifier not in group.children:
                        group.set_child(group)
                    operator.metadata.update_data("group_id", group.metadata.identifier)
                    await self.post_notice(
                        "RelationshipSevered",
                        member,
                        ev_type,
                        {"group": group},
                        operator=operator,
                        relationship="Member"
                    )
            else:
                member = self.include_member(member_data)
                if member.metadata.identifier not in group.children:
                    group.set_child(member)
                member.metadata.update_data("group_id", group.metadata.identifier)

                if ev_type.endswith('MemberJoinEvent'):
                    await self.post_notice(
                        "RelationshipSetup",
                        member,
                        ev_type,
                        data,
                        relationship="Member"
                    )
                elif ev_type in ("MemberCardChangeEvent", "MemberSpecialTitleChangeEvent", "MemberHonorChangeEvent"):
                    await self.post_notice(
                        "MonomerMetadataUpdate",
                        member,
                        ev_type,
                        data
                    )
                elif ev_type == "MemberPermissionChangeEvent":
                    await self.post_notice(
                        "MonomerStatusUpdate",
                        member,
                        ev_type,
                        data
                    )
                else:
                    operator_data = data.pop('operator')
                    operator = self.include_member(operator_data)
                    if operator.metadata.identifier not in group.children:
                        group.set_child(group)
                    operator.metadata.update_data("group_id", group.metadata.identifier)
                    await self.post_notice(
                        "MonomerStatusUpdate",
                        member,
                        ev_type,
                        data,
                        operator=operator,
                        active=False,
                        action=ev_type.replace('Member', '').replace('Event', '')
                    )
        elif ev_type.endswith("RequestEvent"):
            friend = self.temp_monomer(data.pop('nick'), data.pop('fromId'))
            await self.post_request(
                "RequestReceived",
                friend,
                ev_type,
                data,
                str(data.pop("eventId"))
            )

    def temp_monomer(self, name: str, identifier: str):
        friend = MiraiMonomer(
            self.scene.monomer_protocol,
            name,
            identifier,
        )
        friend.set_parent(self.scene.edoves.self)
        return friend

    def include_friend(self, friend_data):
        friend_id = str(friend_data.get('id'))
        if not (friend := self.scene.monomers.get(friend_id)):
            friend = MiraiMonomer(
                self.scene.monomer_protocol,
                friend_data.get("nickname"),
                friend_id,
                friend_data.get("remark")
            )
            friend.set_parent(self.scene.edoves.self)
            self.scene.monomers.setdefault(friend.metadata.identifier, friend)
        if friend.prime_tag == "Member":
            friend.set_parent(self.scene.edoves.self)
            friend.metadata.update_data("name", friend_data.get("nickname"))
            friend.metadata.update_data("alias", friend_data.get("remark"))
        friend.set_prime_tag("Friend")
        return friend

    def exclude_friend(self, friend_data):
        friend_id = str(friend_data.get('id'))
        if not (friend := self.scene.monomers.get(friend_id)):
            friend = MiraiMonomer(
                self.scene.monomer_protocol,
                friend_data.get("nickname"),
                friend_id,
                friend_data.get("remark")
            )
        else:
            self.scene.edoves.self.children.pop(friend.metadata.identifier)
            if not friend.compare("Member"):
                self.scene.monomers.pop(friend.metadata.identifier)
        friend.set_prime_tag("Friend")
        return friend

    def include_member(self, member_data):
        member_id = str(member_data.get('id'))
        if not (member := self.scene.monomers.get(member_id)):
            member = MiraiMonomer(
                self.scene.monomer_protocol,
                member_data.get("memberName"),
                member_id,
                **{
                    "permission": member_data.get("permission"),
                    "specialTitle": member_data.get("specialTitle"),
                    "joinTimestamp": member_data.get("joinTimestamp"),
                    "lastSpeakTimestamp": member_data.get("lastSpeakTimestamp"),
                    "muteTimeRemaining": member_data.get("muteTimeRemaining"),
                }
            )
            self.scene.monomers[member.metadata.identifier] = member
        elif member.prime_tag == "Member":
            member.metadata.update_data("name", member_data.get("memberName"))
            member.metadata.update_data("permission", member_data.get("permission"))
            member.metadata.update_data("specialTitle", member_data.get("specialTitle"))
            member.metadata.update_data("joinTimestamp", member_data.get("joinTimestamp"))
            member.metadata.update_data("lastSpeakTimestamp", member_data.get("lastSpeakTimestamp"))
            member.metadata.update_data("muteTimeRemaining", member_data.get("muteTimeRemaining"))
        elif member.prime_tag == "Friend":
            member.metadata.alias = member.metadata.name
            member.metadata.update_data("name", member_data.get("memberName"))
        member.set_prime_tag("Member")
        return member

    def exclude_member(self, member_data, group_id: str):
        member_id = str(member_data.get('id'))
        if not (member := self.scene.monomers.get(member_id)):
            member = MiraiMonomer(
                self.scene.monomer_protocol,
                member_data.get("memberName"),
                member_id,
                **{
                    "permission": member_data.get("permission"),
                    "specialTitle": member_data.get("specialTitle"),
                    "joinTimestamp": member_data.get("joinTimestamp"),
                    "lastSpeakTimestamp": member_data.get("lastSpeakTimestamp"),
                    "muteTimeRemaining": member_data.get("muteTimeRemaining"),
                }
            )
        else:
            member.parents[group_id].children.pop(member_id)
            member.parents.pop(group_id)
            if not member.compare('Friend') and not member.parents:
                self.scene.monomers.pop(member_id)
        member.set_prime_tag("Member")
        return member

    def include_group(self, group_data):
        group_id = str(group_data.get('id'))
        if not (group := self.scene.monomers.get(group_id)):
            group = MiraiMonomer(
                self.scene.monomer_protocol,
                group_data.get("name"),
                group_id,
                **{
                    "permission": group_data.get("permission"),
                }
            )
            self.scene.monomers.setdefault(group.metadata.identifier, group)
            group.set_child(self.scene.edoves.self)
        else:
            group.metadata.update_data("name", group_data.get("name"))
            group.metadata.update_data("permission", group_data.get("permission"))
        group.set_prime_tag("Group")
        return group

    def exclude_group(self, group_data):
        group_id = str(group_data.get('id'))
        if not (group := self.scene.monomers.get(group_id)):
            group = MiraiMonomer(
                self.scene.monomer_protocol,
                group_data.get("name"),
                group_id,
                **{
                    "permission": group_data.get("permission"),
                }
            )
        else:
            self.scene.monomers.pop(group.metadata.identifier)
            self.scene.edoves.self.parents.pop(group.metadata.identifier)

            for i, m in group.children.items():
                if m.compare("Friend"):
                    continue
                self.scene.monomers.pop(i)
        group.set_prime_tag("Group")
        return group

    async def post_message(
            self,
            ev_type: str,
            purveyor: "Monomer",
            medium_type: str,
            content: List[Dict[str, str]],
            **kwargs
    ):
        await self.scene.module_protocol.set_medium(
            Message().create(purveyor, MessageChain.parse_obj(content), medium_type)
        )
        await self.scene.module_protocol.broadcast_medium(ev_type, **kwargs)

    async def post_notice(
            self,
            ev_type: str,
            purveyor: "Monomer",
            medium_type: str,
            content: Dict[str, str],
            operator: Optional["Monomer"] = None,
            **kwargs
    ):
        notice = Notice().create(purveyor, content, medium_type)
        if operator:
            notice.operator = operator
        await self.scene.module_protocol.set_medium(notice)
        await self.scene.module_protocol.broadcast_medium(ev_type, **kwargs)

    async def post_request(
            self,
            ev_type: str,
            purveyor: "Monomer",
            medium_type: str,
            content: Dict[str, str],
            event_id: str,
            **kwargs
    ):
        request = Request().create(purveyor, content, medium_type, event=event_id)
        await self.scene.module_protocol.set_medium(request)
        await self.scene.module_protocol.broadcast_medium(ev_type, **kwargs)

    source_information = DataSourceInfo(
        platform="Tencent",
        name="mirai-api-http",
        version="default"
    )
    medium_type = Dict
