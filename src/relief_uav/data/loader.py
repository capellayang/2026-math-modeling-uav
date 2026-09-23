"""Strict, read-only parsing of the five official source workbooks."""

from collections import Counter, defaultdict
from pathlib import Path
from openpyxl import load_workbook

from .models import (
    BatteryStock, CargoBox, CommunicationParameters, Demand, EnergyComponentStock,
    Node, RadioInterface, RelayDrone, RelayModel, Scenario, TransportDrone,
    TransportModel,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SOURCE_DIR = Path("数据") / "无人机应急物资运输基础数据"


class SourceDataError(ValueError):
    """The official workbook no longer matches the expected data contract."""


def _sheet(root: Path, filename: str, sheet_name: str = "数据"):
    path = root / SOURCE_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(path)
    workbook = load_workbook(path, read_only=True, data_only=True)
    if sheet_name not in workbook:
        workbook.close()
        raise SourceDataError(f"Missing {filename}!{sheet_name}")
    try:
        return list(workbook[sheet_name].values)
    finally:
        workbook.close()


def _block(rows, header_row: int, first: int, last: int, expected: tuple[str, ...]):
    header = tuple(rows[header_row - 1][: len(expected)])
    if header != expected:
        raise SourceDataError(f"Unexpected header at row {header_row}: {header!r}")
    result = []
    for number in range(first, last + 1):
        record = rows[number - 1][: len(expected)]
        if len(record) != len(expected) or any(v is None for v in record[:2]):
            raise SourceDataError(f"Incomplete record at row {number}")
        result.append(record)
    return result


def _percent(value: float) -> float:
    result = float(value) / 100.0
    if not 0 <= result <= 1:
        raise SourceDataError(f"Invalid percent value: {value}")
    return result


def _unique(records, field: str):
    result = {}
    for record in records:
        key = getattr(record, field)
        if key in result:
            raise SourceDataError(f"Duplicate {field}: {key}")
        result[key] = record
    return result


def load_scenario(root: Path | None = None) -> Scenario:
    root = Path(root or PROJECT_ROOT).resolve()
    rows = _sheet(root, "调度中心与服务区.xlsx")
    dispatch_header = ("调度中心编号", "调度中心名称", "经度（°）", "纬度（°）", "海拔（m）")
    service_header = ("服务区编号", "服务区名称", "经度（°）", "纬度（°）", "海拔（m）", "本次需保障人口（人）")
    dispatch_row = _block(rows, 2, 3, 3, dispatch_header)[0]
    dispatch = Node(*dispatch_row)
    services = _unique((Node(*r) for r in _block(rows, 6, 7, 21, service_header)), "node_id")

    rows = _sheet(root, "物资需求与配送时限.xlsx")
    demand_header = ("服务区编号", "物资类型", "总需求箱数", "首批必须送达箱数", "单箱质量（kg）", "单箱体积（m³）", "应急优先系数", "首批截止时间（s）", "期望送达时间（s）")
    demands = tuple(Demand(*r) for r in _block(rows, 1, 2, 54, demand_header))
    rows = _sheet(root, "物资需求与配送时限.xlsx", "逐箱货箱清单")
    box_header = ("货箱编号", "服务区编号", "物资类型", "单箱质量（kg）", "单箱体积（m³）", "是否首批保障", "首批截止时间（s）", "期望送达时间（s）", "应急优先系数")
    box_records = []
    for r in _block(rows, 1, 2, 81, box_header):
        if r[5] not in ("是", "否"):
            raise SourceDataError(f"Unknown first-batch flag: {r[5]}")
        box_records.append(CargoBox(r[0], r[1], r[2], float(r[3]), float(r[4]), r[5] == "是", r[6], float(r[7]), float(r[8])))
    boxes = _unique(box_records, "box_id")

    rows = _sheet(root, "运输无人机数据.xlsx")
    transport_header = ("机型编号", "机型名称", "含电池空载总质量（kg）", "最大载货质量（kg）", "可用装载体积（m³）", "计划巡航速度（m/s）", "空载标准航程（m）", "满载标准航程（m）", "电池可用能量（kWh）", "返航电量下限（%）", "工位固定准备时间（s）", "每箱装载时间（s）", "接收点基础交接时间（s）", "每箱增加交接时间（s）", "最大爬升速度（m/s）", "最大下降速度（m/s）", "爬升能耗效率", "下降能耗效率")
    transport_models = _unique((TransportModel(*r[:9], _percent(r[9]), *r[10:]) for r in _block(rows, 2, 3, 5, transport_header)), "model_id")
    drone_header = ("无人机编号", "机型编号", "初始位置")
    transport_drones = _unique((TransportDrone(*r) for r in _block(rows, 8, 9, 16, drone_header)), "drone_id")
    battery_header = ("机型编号", "共享电池组总数（组）", "等效完全充电时间（s）")
    battery_stocks = _unique((BatteryStock(*r) for r in _block(rows, 19, 20, 22, battery_header)), "model_id")

    rows = _sheet(root, "中继无人机数据.xlsx")
    relay_header = ("机型编号", "机型名称", "含能源组件空载总质量（kg）", "中继通信模块质量（kg）", "计划起飞总质量（kg）", "计划巡航速度（m/s）", "巡航功率（kW）", "能源组件可用能量（kWh）", "返航电量下限（%）", "工位固定准备时间（s）", "建链时间（s）", "架次周转时间（s）", "最大爬升速度（m/s）", "最大下降速度（m/s）", "爬升能耗效率", "下降能耗效率", "悬停功率（kW）", "通信附加功率（kW）", "最大悬停离地高度（m）")
    relay_models = _unique((RelayModel(*r[:8], _percent(r[8]), *r[9:]) for r in _block(rows, 2, 3, 3, relay_header)), "model_id")
    relay_drone_header = ("中继无人机编号", "机型编号", "初始位置")
    relay_drones = _unique((RelayDrone(*r) for r in _block(rows, 6, 7, 8, relay_drone_header)), "drone_id")
    component_header = ("机型编号", "共享能源组件总数（组）", "等效完全充电时间（s）")
    component_stocks = _unique((EnergyComponentStock(*r) for r in _block(rows, 11, 12, 12, component_header)), "model_id")

    rows = _sheet(root, "通信链路参数.xlsx")
    radio = {}
    for row_number in range(3, 17):
        category, name, _, symbol, value = rows[row_number - 1][:5]
        key = (category, name)
        if key in radio or value is None:
            raise SourceDataError(f"Invalid radio parameter: {key}")
        radio[key] = float(value)

    def value(category, name):
        try:
            return radio[(category, name)]
        except KeyError as exc:
            raise SourceDataError(f"Missing communication parameter {category}/{name}") from exc

    def interface(category):
        return RadioInterface(value(category, "发射功率（dBm）"), value(category, "天线增益（dBi）"))

    communication = CommunicationParameters(
        value("传播参数", "载波频率（MHz）"), value("传播参数", "系统损耗（dB）"),
        value("传播参数", "地形遮挡附加损耗（dB）"), value("接收参数", "接收灵敏度（dBm）"),
        value("接收参数", "衰落裕量（dB）"), interface("运输无人机"),
        interface("中继接入端"), interface("中继回传端"), interface("固定网关 G01"),
        value("固定网关 G01", "天线离地高度（m）"),
    )
    scenario = Scenario(root, dispatch, services, demands, boxes, transport_models, transport_drones,
                        battery_stocks, relay_models, relay_drones, component_stocks, communication)
    _validate(scenario)
    return scenario


def _validate(s: Scenario) -> None:
    if s.dispatch.node_id != "O01" or len(s.services) != 15 or len(s.boxes) != 80:
        raise SourceDataError("Unexpected standard scenario size or dispatch identifier")
    for b in s.boxes.values():
        if b.service_id not in s.services or b.mass_kg <= 0 or b.volume_m3 <= 0 or b.desired_delivery_s <= 0:
            raise SourceDataError(f"Invalid cargo box: {b.box_id}")
        if b.first_batch != (b.first_batch_deadline_s is not None):
            raise SourceDataError(f"First-batch deadline mismatch: {b.box_id}")
    by_key = defaultdict(list)
    for b in s.boxes.values():
        by_key[(b.service_id, b.material_type)].append(b)
    if len(s.demands) != len(by_key):
        raise SourceDataError("Demand/box group count mismatch")
    for d in s.demands:
        group = by_key[(d.service_id, d.material_type)]
        if (len(group), sum(b.first_batch for b in group)) != (d.box_count, d.first_batch_count):
            raise SourceDataError(f"Demand/box counts differ: {d.service_id}/{d.material_type}")
        for b in group:
            if (b.mass_kg, b.volume_m3, b.emergency_priority, b.desired_delivery_s) != (d.mass_per_box_kg, d.volume_per_box_m3, d.emergency_priority, d.desired_delivery_s):
                raise SourceDataError(f"Demand/box values differ: {b.box_id}")
            if b.first_batch and b.first_batch_deadline_s != d.first_batch_deadline_s:
                raise SourceDataError(f"Demand/box deadline differs: {b.box_id}")
    if any(d.model_id not in s.transport_models or d.initial_node_id != "O01" for d in s.transport_drones.values()):
        raise SourceDataError("Transport drone references missing model or initial node")
    if any(d.model_id not in s.relay_models or d.initial_node_id != "O01" for d in s.relay_drones.values()):
        raise SourceDataError("Relay drone references missing model or initial node")
    if set(s.battery_stocks) != set(s.transport_models) or set(s.component_stocks) != set(s.relay_models):
        raise SourceDataError("Energy resource model references differ")
    drone_counts = Counter(d.model_id for d in s.transport_drones.values())
    if any(stock.count < drone_counts[model] for model, stock in s.battery_stocks.items()):
        raise SourceDataError("Initial transport batteries fewer than drones")
    relay_counts = Counter(d.model_id for d in s.relay_drones.values())
    if any(stock.count < relay_counts[model] for model, stock in s.component_stocks.items()):
        raise SourceDataError("Initial relay components fewer than drones")
