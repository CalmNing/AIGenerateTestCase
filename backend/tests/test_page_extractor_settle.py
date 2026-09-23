"""落稳等待策略测试。

回归背景:采集前的等待原本是 `domcontentloaded` + `wait_for_load_state("networkidle")`。
微前端(wujie/qiankun)把子应用放在 iframe + shadow root 里异步挂载,主应用加载完会
有一个静默窗口,networkidle 在此期间就满足并返回,采集只拿到外壳 —— 实测同一页面
连续执行命中数在 2 / 153 / 306 之间随机波动。

改成轮询「穿透 shadow root 的交互元素数」后,这里守住四件事:
  1. 判稳靠计数不变,不是靠固定的时间/网络空闲;
  2. 计数为 0 时不判稳(页面还没开始渲染);
  3. 预热期内不判稳 —— 子应用挂载前主应用外壳会长时间稳定在个位数元素上;
  4. 判据必须穿透 shadow root —— 顶层计数在 wujie 场景下恒为 1。

全程不碰真浏览器,用假的 page/frame 驱动计数序列。
"""
import asyncio
import ast
import inspect
import textwrap
import time

from app.services import page_extractor
from app.services.page_extractor import (
    PAGE_COUNT_JS,
    SETTLE_MIN_WAIT_MS,
    SETTLE_QUIET_MS,
    SETTLE_STABLE_ROUNDS,
    SETTLE_TIMEOUT_MS,
    _count_interactive,
    _NetworkActivity,
    _wait_for_settle,
)


def _body_source(func) -> str:
    """取函数体源码,剥掉 docstring。

    docstring 里会提到 networkidle 以说明为什么不用它,直接搜原文会误命中。
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    body = tree.body[0].body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return "\n".join(ast.unparse(node) for node in body)


class _FakeFrame:
    """按预设序列吐计数值,序列耗尽后一直重复最后一个值。"""

    def __init__(self, seq, fail=False):
        self._seq = list(seq)
        self._i = 0
        self.calls = 0
        self.fail = fail

    async def evaluate(self, _js):
        if self.fail:
            raise RuntimeError("frame detached")
        self.calls += 1
        value = self._seq[min(self._i, len(self._seq) - 1)]
        self._i += 1
        return value


class _FakePage:
    def __init__(self, frames):
        self.frames = list(frames)
        self.main_frame = self.frames[0] if self.frames else None


def _run(coro):
    """在独立事件循环里跑协程,且不触碰线程的 current event loop。

    不能用 asyncio.run():它结束时会把 current event loop 置空
    (policy.set_event_loop(None)),后续依赖 asyncio.get_event_loop() 的测试
    在全量执行时会失败 —— 实测会带崩 test_ui_automation_routes 的 5 个用例
    (单文件跑全过,全量跑必挂)。
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _settle(page, **kwargs):
    """测试默认关掉预热期,单测只关心判稳逻辑本身。"""
    kwargs.setdefault("interval_ms", 1)
    kwargs.setdefault("timeout_ms", 300)
    kwargs.setdefault("min_wait_ms", 0)
    return _run(_wait_for_settle(page, **kwargs))


class TestWaitForSettle:
    def test_returns_once_count_holds_still(self):
        """计数落稳后立即返回,不等满超时。"""
        frame = _FakeFrame([1, 1, 5, 5, 5, 5, 5, 5])
        assert _settle(_FakePage([frame]), stable_rounds=3) == 5
        # 前两轮为 1 时还不够稳定轮数,第三轮起计数 5 才连续累加
        assert frame.calls == 2 + 3 + 1

    def test_zero_count_is_never_treated_as_stable(self):
        """计数恒为 0 时不能判稳 —— 页面可能还没开始渲染,必须等到超时。"""
        frame = _FakeFrame([0])
        assert _settle(_FakePage([frame]), timeout_ms=40) == 0
        assert frame.calls > SETTLE_STABLE_ROUNDS, "计数 0 被误判为稳定,会漏采空壳页面"

    def test_transient_empty_page_does_not_return_early(self):
        """先空后满:中间的空档不能触发提前返回。"""
        frame = _FakeFrame([0, 0, 0, 0, 12, 12, 12, 12])
        assert _settle(_FakePage([frame])) == 12

    def test_times_out_when_count_never_holds_still(self):
        frame = _FakeFrame(list(range(1000)))  # 每轮都变,永不判稳
        result = _settle(_FakePage([frame]), timeout_ms=40)
        assert result > 0
        assert frame.calls > SETTLE_STABLE_ROUNDS

    def test_stable_rounds_is_configurable(self):
        frame = _FakeFrame([7])
        _settle(_FakePage([frame]), stable_rounds=2, timeout_ms=200)
        assert frame.calls == 3  # 首轮 + 2 轮稳定,不是默认的 3 轮

    def test_default_budget_is_bounded(self):
        params = inspect.signature(_wait_for_settle).parameters
        assert params["timeout_ms"].default == SETTLE_TIMEOUT_MS
        assert params["stable_rounds"].default == SETTLE_STABLE_ROUNDS
        assert params["min_wait_ms"].default == SETTLE_MIN_WAIT_MS
        assert params["quiet_ms"].default == SETTLE_QUIET_MS


class TestNetworkQuiet:
    def test_busy_network_blocks_settling(self):
        """网络还在活动时不判稳。

        子应用挂载耗时波动大(实测同一页面 4~8s),固定预热期兜不住 —— 只要还在
        拉 chunk,计数即使长期停在个位数也不能判稳(否则采回空壳,实测命中 18)。
        """
        activity = _NetworkActivity()
        frame = _FakeFrame([2])

        async def run():
            async def keep_busy():
                while True:
                    activity.touch()
                    await asyncio.sleep(0.005)

            busy = asyncio.create_task(keep_busy())
            try:
                started = time.monotonic()
                result = await _wait_for_settle(
                    _FakePage([frame]),
                    activity=activity,
                    quiet_ms=80,
                    min_wait_ms=0,
                    interval_ms=1,
                    timeout_ms=150,
                )
                return result, time.monotonic() - started
            finally:
                busy.cancel()

        result, elapsed = _run(run())
        assert result == 2
        assert elapsed >= 0.12, "网络仍活跃时提前判稳了"

    def test_quiet_network_allows_settling(self):
        """网络已安静时正常判稳,不被 quiet 条件拖到超时。"""
        activity = _NetworkActivity()
        activity.last = time.monotonic() - 10
        frame = _FakeFrame([7])
        assert _settle(_FakePage([frame]), activity=activity) == 7

    def test_absent_activity_falls_back_to_count_only(self):
        """不传 activity 时退化为纯计数判稳(保留给不关心网络的调用方)。"""
        frame = _FakeFrame([9])
        assert _settle(_FakePage([frame])) == 9

    def test_activity_records_latest_touch(self):
        activity = _NetworkActivity()
        activity.last = time.monotonic() - 5
        assert activity.quiet_ms() > 4000
        activity.touch()
        assert activity.quiet_ms() < 1000


class TestMinWaitWarmup:
    def test_holds_off_until_warmup_elapses(self):
        """预热期内即使计数已稳定也不返回。"""
        import time

        frame = _FakeFrame([2])
        started = time.monotonic()
        assert _settle(_FakePage([frame]), min_wait_ms=120, timeout_ms=600) == 2
        assert time.monotonic() - started >= 0.12

    def test_shell_count_does_not_settle_before_subapp_mounts(self):
        """回归:子应用挂载前,主应用外壳会稳定在个位数元素上,不能就此判稳。

        实测该站点 t=0~3s 恒为 2 个元素、t=4s 才跳到 202 个。预热期不足会在 2 上
        判稳并采回空壳页面(实测命中 18 而非 306)。这里用「前 6 轮都是 2,之后
        跳到 202」复现该时序:采样间隔取 5ms、预热期 200ms,让外壳阶段(约 30ms)
        稳稳落在预热期内 —— 若预热期失效,结果会停在 2 上。
        """
        frame = _FakeFrame([2] * 6 + [202])
        result = _settle(
            _FakePage([frame]), min_wait_ms=200, interval_ms=5, timeout_ms=1000
        )
        assert result == 202, "在外壳元素数上提前判稳了"


class TestCountInteractive:
    def test_sums_across_frames(self):
        """跨 frame 累加 —— 子应用元素可能来自不同 frame。"""
        page = _FakePage([_FakeFrame([2]), _FakeFrame([3])])
        assert _run(_count_interactive(page)) == 5

    def test_broken_frame_is_skipped_not_fatal(self):
        """单个 frame 卸载/跨域不能让整轮轮询崩掉。"""
        page = _FakePage([_FakeFrame([4]), _FakeFrame([0], fail=True), _FakeFrame([1])])
        assert _run(_count_interactive(page)) == 5

    def test_empty_page_is_zero(self):
        assert _run(_count_interactive(_FakePage([]))) == 0


class TestShadowRootTraversal:
    def test_count_js_traverses_shadow_root(self):
        """判据脚本必须穿透 shadow root。

        wujie 子应用 DOM 全在 <wujie-app> 的 shadow root 内,顶层
        document.querySelectorAll 恒为 1 —— 用它做判据会立刻误判"已稳定",
        重新引入随机漏采。
        """
        assert "shadowRoot" in PAGE_COUNT_JS
        assert "queryAllDeep" in PAGE_COUNT_JS

    def test_extract_elements_waits_for_settle_instead_of_networkidle(self):
        """回归守卫:等待策略不能退回 networkidle。"""
        body = _body_source(page_extractor.extract_elements)
        assert "_wait_for_settle" in body
        assert "networkidle" not in body
        assert "wait_for_load_state" not in body
