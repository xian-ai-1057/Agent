"""範例專案的整合測試：既有功能包成工具後，agent 真的能用。"""

import pathlib

import pytest

import harness
from tests import conftest

EXAMPLE = pathlib.Path(__file__).parent.parent / "examples" / "report_bot"


@pytest.fixture(scope="module")
def report_bot():
    """載入範例專案。"""
    return harness.load_project(EXAMPLE)


async def test_report_bot_builds_report_through_tool(report_bot, fake_model):
    model = fake_model(
        [
            conftest.tool_response(
                ("build_sales_report", {"month": "2026-10"})
            ),
            conftest.text_response("報告如上"),
        ]
    )

    await harness.run(report_bot.agents["main"], "給我十月報告", model_fn=model)

    report = model.requests[1]["messages"][-1]["content"]
    assert "# 2026-10 銷售報告" in report
    assert "| **合計** | **192,000** |" in report


async def test_report_bot_tells_model_when_month_has_no_data(
    report_bot, fake_model
):
    model = fake_model(
        [
            conftest.tool_response(("get_sales_summary", {"month": "1999-01"})),
            conftest.text_response("沒有資料"),
        ]
    )

    await harness.run(report_bot.agents["main"], "1999 年呢", model_fn=model)

    content = model.requests[1]["messages"][-1]["content"]
    assert content.startswith("[錯誤] ValueError: 1999-01 沒有銷售資料")


def test_report_bot_write_file_needs_approval(report_bot):
    tools = {item.name: item for item in report_bot.agents["main"].tools}

    assert tools["write_file"].needs_approval
    assert not tools["build_sales_report"].needs_approval
