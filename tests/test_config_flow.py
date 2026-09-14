"""Exercise connection changes through Home Assistant's flow manager."""

from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fellow_stagg_pro.api import FellowStaggProApiError
from custom_components.fellow_stagg_pro.const import DOMAIN

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.usefixtures("enable_custom_integrations"),
]


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="192.0.2.10",
        title="Fellow Stagg Pro (192.0.2.10)",
        data={
            "host": "192.0.2.10",
            "port": 80,
            "scan_interval": 30,
            "enable_heat_control": False,
            "enable_set_temperature": False,
        },
        options={"scan_interval": 15, "enable_heat_control": False},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def get_state():
    with patch(
        "custom_components.fellow_stagg_pro.api.FellowStaggProApi.async_get_state",
        new_callable=AsyncMock,
        return_value={"mode": "off"},
    ) as get_state:
        yield get_state


async def test_reconfigure_form(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["data_schema"]({}) == {"host": "192.0.2.10", "port": 80}
    for invalid in ({"host": "  "}, {"port": 0}, {"port": 65536}):
        with pytest.raises(vol.Invalid):
            result["data_schema"](invalid)


async def test_reconfigure_form_serializes_for_frontend(
    hass: HomeAssistant, entry: MockConfigEntry, hass_client
) -> None:
    await async_setup_component(hass, "config", {})
    client = await hass_client()
    response = await client.post(
        "/api/config/config_entries/flow",
        json={"handler": DOMAIN, "entry_id": entry.entry_id},
    )

    assert response.status == 200
    result = await response.json()
    assert result["step_id"] == "reconfigure"
    fields = {field["name"]: field for field in result["data_schema"]}
    assert fields["host"]["default"] == "192.0.2.10"
    assert fields["port"]["default"] == 80


@pytest.mark.parametrize("title", ["Fellow Stagg Pro (192.0.2.10)", "Kitchen kettle"])
async def test_reconfigure_preserves_entry(
    hass: HomeAssistant, entry: MockConfigEntry, get_state: AsyncMock, title: str
) -> None:
    hass.config_entries.async_update_entry(entry, title=title)
    original_data = dict(entry.data)
    original_options = dict(entry.options)

    with patch.object(hass.config_entries, "async_reload", return_value=True) as reload:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
            data={"host": " 192.0.2.20 ", "port": 8080},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert hass.config_entries.async_entries(DOMAIN) == [entry]
    assert entry.unique_id == "192.0.2.10"
    assert entry.data == {**original_data, "host": "192.0.2.20", "port": 8080}
    assert entry.options == original_options
    assert entry.title == (
        "Kitchen kettle"
        if title == "Kitchen kettle"
        else "Fellow Stagg Pro (192.0.2.20)"
    )
    get_state.assert_awaited_once()
    reload.assert_awaited_once_with(entry.entry_id)


async def test_failed_connection_can_be_corrected(
    hass: HomeAssistant, entry: MockConfigEntry, get_state: AsyncMock
) -> None:
    get_state.side_effect = FellowStaggProApiError("unreachable")
    with patch.object(hass.config_entries, "async_reload", return_value=True) as reload:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
            data={"host": "192.0.2.20", "port": 8080},
        )
        assert result["errors"] == {"base": "cannot_connect"}
        assert result["data_schema"]({}) == {"host": "192.0.2.20", "port": 8080}
        assert entry.data["host"] == "192.0.2.10"
        assert entry.data["port"] == 80
        reload.assert_not_called()

        get_state.side_effect = None
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": "192.0.2.30", "port": 80}
        )
        await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert entry.data["host"] == "192.0.2.30"
    reload.assert_awaited_once_with(entry.entry_id)


async def test_reconfigure_rejects_another_kettle(
    hass: HomeAssistant, entry: MockConfigEntry, get_state: AsyncMock
) -> None:
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="original-host",
        data={"host": "Other-Kettle", "port": 80},
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
        data={"host": "other-kettle", "port": 80},
    )

    assert result["errors"] == {"base": "already_configured"}
    assert entry.data["host"] == "192.0.2.10"
    get_state.assert_not_awaited()


async def test_setup_rejects_reconfigured_host(
    hass: HomeAssistant, entry: MockConfigEntry, get_state: AsyncMock
) -> None:
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "host": "192.0.2.20"}
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=dict(entry.data)
    )

    assert result["reason"] == "already_configured"
    get_state.assert_not_awaited()


async def test_options_still_reload(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    with patch.object(hass.config_entries, "async_reload", return_value=True) as reload:
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "scan_interval": 20,
                "enable_heat_control": False,
                "enable_set_temperature": False,
            },
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["scan_interval"] == 20
    reload.assert_awaited_once_with(entry.entry_id)
