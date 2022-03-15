import asyncio
import importlib
from contextlib import contextmanager
from inspect import isclass, getmembers
import shelve
from os import PathLike
from pathlib import Path
from typing import Generic, TYPE_CHECKING, Optional, Type, Dict, TypeVar, Union, cast, Coroutine, List
from .monomer import MonoMetaComponent, Monomer
from .context import current_scene
from ..builtin.behavior import MiddlewareBehavior
from ..builtin.medium import DictMedium
from .utilles import IOStatus, SceneStatus
from .typings import TProtocol, TConfig
from .exceptions import ValidationFailed
from .module import BaseModule
from .interact import InteractiveObject, IOManager

TMde = TypeVar("TMde", bound=BaseModule)

if TYPE_CHECKING:
    from . import Edoves


class EdovesMetadata(MonoMetaComponent):
    io: "EdovesSelf"


class EdovesMainBehavior(MiddlewareBehavior):
    io: "EdovesSelf"
    loop: asyncio.AbstractEventLoop

    async def start(self):
        connected = await self.io.metadata.protocol.push_medium(
            {"start": True},
            lambda x: DictMedium().create(self.io, x)
        )
        await self.io.metadata.protocol.broadcast_medium("DockerOperate")
        await connected.wait_response()

    def activate(self):
        self.io.metadata.protocol.scene.monomers.append(self.io.metadata.pure_id)
        self.loop = self.io.metadata.protocol.scene.edoves.event_system.loop
        self.io.add_tags("bot", self.io.metadata.protocol.source_information.name, "app")


class EdovesSelf(Monomer):
    prefab_metadata = EdovesMetadata
    prefab_behavior = EdovesMainBehavior


class EdovesScene(Generic[TProtocol]):
    __name: str
    protagonist: EdovesSelf
    edoves: "Edoves"
    protocol: TProtocol
    config: TConfig
    modules: List[str]
    monomers: List[str]
    status: SceneStatus

    def __init__(
            self,
            name: str,
            edoves: "Edoves",
            config: TConfig
    ):
        self.modules = []  # 不存储protocol的标识符
        self.monomers = []  # 不存储protocol的标识符
        self.status = SceneStatus.STOPPED
        self.__name = name
        self.edoves = edoves
        self.config = config
        self.protocol = config.protocol(self, config)
        self.cache_path = f"./edoves_cache/{self.protocol.source_information.name}/{self.scene_name}/"
        with self.context() as self_scene:
            self_scene.require_modules(path=config.modules_base_path)

        self.protagonist = EdovesSelf(
            self.protocol,
            "Edoves Application",
            self.config.get("account"),
            "edoves"
        )
        try:
            self.protocol.docker = self.protocol.docker_type(self.protocol, self.config.client())
            self.edoves.logger.debug(
                f"{self.scene_name}: "
                f"{self.server_docker.__class__.__name__} activate successful"
            )
        except ValidationFailed:
            self.edoves.logger.warning(
                f"{self.scene_name}: "
                f"{self.server_docker.__class__.__name__} does not supply the dock server you chosen"
            )

    @property
    def scene_name(self) -> str:
        return self.__name

    @property
    def server_docker(self):
        return self.protocol.docker

    @property
    def all_io(self) -> Dict[str, "InteractiveObject"]:
        return {**self.monomer_map, "server": self.protocol.docker, **self.module_map}

    @contextmanager
    def context(self):
        token = current_scene.set(self)
        yield self
        current_scene.reset(token)

    @classmethod
    def current(cls) -> "EdovesScene":
        return cast(EdovesScene, current_scene.get())

    @property
    def monomer_map(self) -> Dict[str, "Monomer"]:
        """带有protocol标识符"""
        return {
            mono.metadata.identifier: mono
            for mono in IOManager.filter(Monomer)
            if mono.metadata.pure_id in self.monomers
        }

    @property
    def module_map(self) -> Dict[str, "BaseModule"]:
        """带有protocol标识符"""
        return {
            module.metadata.identifier: module
            for module in IOManager.filter(BaseModule)
            if module.metadata.pure_id in self.modules
        }

    def save_snap(self):
        path = Path(self.cache_path)
        if not path.exists():
            path.mkdir()
        relation_table = {}
        monomers = self.monomer_map
        for i, m in monomers.items():
            relation_table[i] = {'parents': m.relation['parents'], 'children': m.relation['children']}
        monomer = {k.split('@')[0]: v for k, v in monomers.items() if k.split('@')[0] != str(self.config.account)}
        with shelve.open(f"{self.cache_path}/monomerSnap.db") as db:
            db['rtable'] = relation_table
            db['monomer'] = monomer
        self.edoves.logger.debug(f"{self.scene_name}: save monomerSnap.db in {self.cache_path}")

    def load_snap(self):
        try:
            db = shelve.open(f"{self.cache_path}/monomerSnap.db")
            try:
                monomers = cast(Dict, db['monomer'])
                self.monomers.extend(list(monomers.keys()))
                r_table = db['rtable']
            except (KeyError, ModuleNotFoundError):
                db.close()
                Path(f"{self.cache_path}/monomerSnap.db").unlink()
                return
            for i, r in r_table.items():
                m = self.monomer_map.get(i)
                for ri in r['parents']:
                    m.set_parent(self.monomer_map.get(ri))
                for ri in r['children']:
                    m.set_child(self.monomer_map.get(ri))
            db.close()
            self.edoves.logger.debug(f"{self.scene_name}: load monomerSnap.db in {self.cache_path}")
        except FileNotFoundError:
            return

    def require(self, path: str) -> Optional[BaseModule]:
        """
        以导入路径方式加载模块

        Args:
            path (str): 模块路径
        """
        try:
            imported_module = importlib.import_module(path, path)
            for _, m in getmembers(
                    imported_module, lambda x: isclass(x) and issubclass(x, BaseModule) and x is not BaseModule
            ):
                return self.require_module(m)
        except ModuleNotFoundError:
            return

    def require_module(self, module_type: Type[TMde]) -> Optional[TMde]:
        """激活单个模块并返回

        Args:
            module_type: 要激活的模块类型, 若模块已激活则返回激活完成的模块
        Returns:
            new_module: 激活完成的模块
        """
        _name = module_type.__qualname__
        _path = module_type.__module__ + '.' + _name
        _id = f"{_path}@{self.protocol.identifier}"
        if m := self.module_map.get(_id):
            return m
        try:
            new_module = module_type(self.protocol)
            if new_module.metadata.state in (IOStatus.CLOSED, IOStatus.UNKNOWN):
                return
            self.modules.append(_path)
            self.edoves.logger.debug(f"{self.scene_name}: {_name} activate successful")
            return new_module
        except ValidationFailed:
            self.edoves.logger.warning(f"{self.scene_name}: {_name} does not supply the dock server you chosen")

    def require_modules(
            self,
            *module_type: Type[BaseModule],
            path: Optional[Union[str, PathLike, Path]] = None
    ) -> None:
        """激活多个模块

        Args:
            module_type: 要激活的多个模块类型, 若有重复则重新激活
            path: 文件路径, 可以是文件夹路径
        """

        count = 0
        for mt in module_type:
            _name = module_type.__qualname__
            _path = module_type.__module__ + '.' + _name
            _id = f"{_path}@{self.protocol.identifier}"
            try:
                nm = mt(self.protocol)
                self.modules.append(_path)
                if nm.metadata.state in (IOStatus.CLOSED, IOStatus.UNKNOWN):
                    return
                self.edoves.logger.debug(f"{self.scene_name}: {_name} activate successful")
                count += 1
            except ValidationFailed:
                self.edoves.logger.warning(f"{self.scene_name}: {_name} does not supply the dock server you chosen")
        if count > 0:
            self.edoves.logger.info(f"{self.scene_name}: {count} modules activate successful")
        if path:
            ignore = ["__init__", "__pycache__"]
            path = path if isinstance(path, Path) else Path(path)
            if path.is_dir():
                for p in path.iterdir():
                    if p.suffix in (".py", "") and p.stem not in ignore:
                        self.require(".".join(p.parts[:-1:1]) + "." + p.stem)
            elif path.is_file():
                self.require(".".join(path.parts[:-1:1]) + "." + path.stem)

    async def start_running(self):
        if self.status is SceneStatus.STOPPED:
            self.status = SceneStatus.STARTING
            self.load_snap()
            self.edoves.logger.info(f"{self.scene_name} Using DataSource: {self.protocol.source_information.info}")
            tasks = []
            for i, v in enumerate(self.all_io.values()):
                if v.metadata.state in (IOStatus.CLOSED, IOStatus.UNKNOWN):
                    continue
                tasks.append(asyncio.create_task(v.behavior.start(), name=f"{self.scene_name}_IO_Start @AllIO[{i}]"))

            try:
                results = await asyncio.gather(*tasks)
                for task in results:
                    if task and task.exception() == NotImplementedError:
                        self.edoves.logger.warning(f"{task}'s behavior in {self.scene_name} start failed.")
            except TimeoutError:
                await self.stop_running()
                return
            await asyncio.sleep(0.001)
            self.edoves.logger.info(f"{len(self.all_io)} InteractiveObjects in {self.scene_name} has been loaded.")
            self.status = SceneStatus.RUNNING

    async def update(self):
        while self.status is SceneStatus.RUNNING:
            await asyncio.sleep(self.config.update_interval)
            tasks = []
            for i, v in enumerate(self.all_io.values()):
                if v.metadata.state == IOStatus.CLOSE_WAIT:
                    v.metadata.state = IOStatus.CLOSED
                if v.metadata.state not in (IOStatus.CLOSED, IOStatus.UNKNOWN):
                    tasks.append(asyncio.create_task(
                        v.behavior.update(), name=f"{self.scene_name}_IO_Update @AllIO[{i}]"
                    ))
            try:
                await asyncio.gather(*tasks)
            except NotImplementedError:
                pass

    async def stop_running(self):
        if self.status in (SceneStatus.STARTING, SceneStatus.RUNNING):
            self.status = SceneStatus.STOPPING
            for k, v in self.all_io.items():
                if v.metadata.state not in (IOStatus.CLOSED, IOStatus.UNKNOWN):
                    try:
                        v.metadata.state = IOStatus.CLOSED
                        await v.behavior.quit()
                    except NotImplementedError:
                        self.edoves.logger.warning(f"{k}'s behavior in {self.scene_name} quit failed.")
            self.status = SceneStatus.CLEANUP
            await self.clean_up()
            self.status = SceneStatus.STOPPED

    async def clean_up(self):
        self.modules.clear()
        self.save_snap()
        for t in asyncio.all_tasks(self.edoves.event_system.loop):
            if t is asyncio.current_task(self.edoves.event_system.loop):
                continue
            coro: Coroutine = t.get_coro()
            try:
                if coro.__qualname__.startswith(f"{self.scene_name}_IO_"):
                    t.cancel()
                    self.edoves.logger.debug(f"Cancelling {t.get_name()} wrapping {coro.__qualname__}")
            except Exception as e:
                self.edoves.logger.warning(f"{self.scene_name}: {(e.__class__, e.args)}")
