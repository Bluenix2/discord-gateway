from urllib.parse import parse_qs, urlsplit
import pytest

from discord_gateway import DiscordConnection


class TestDestination:
    def test_stripped_scheme(self) -> None:
        conn = DiscordConnection('wss://gateway.discord.gg', encoding='json')

        assert conn.destination[0] == 'gateway.discord.gg'

    def test_no_scheme(self) -> None:
        conn = DiscordConnection('gateway.discord.gg', encoding='json')

        assert conn.destination[0] == 'gateway.discord.gg'

    def test_stripped_path(self) -> None:
        conn = DiscordConnection('wss://discord.gg/gateway', encoding='json')

        assert conn.destination[0] == 'discord.gg'

    def test_passed_port(self) -> None:
        conn = DiscordConnection('wss://localhost:11315', encoding='json')

        assert conn.destination == 'localhost', 11315

    def test_default_port(self) -> None:
        conn = DiscordConnection('wss://gateway.discord.gg', encoding='json')

        assert conn.destination[1] == 443


class TestQueryParams:
    @pytest.mark.parametrize('encoding', ('json', 'etf'))
    def test_defaults(self, encoding: str) -> None:
        conn = DiscordConnection('wss://gateway.discord.gg/', encoding=encoding)

        assert parse_qs(conn.query_params) == {'v': 9, 'encoding': encoding}

    def test_compress(self) -> None:
        conn = DiscordConnection(
            'wss://gateway.discord.gg/', encoding='json', compress='zlib-stream'
        )

        assert parse_qs(conn.query_params) == {'v': 9, 'encoding': 'json',
                                               'compress': 'zlib-stream'}
