import asyncio

from textual.app import App, ComposeResult

from apuracao.charts import draw_margin
from apuracao.tracker import State
from apuracao.tui import Chart


def test_log_chart_survives_notification_clicks_and_resize_without_new_data():
    state = State(source='sim', eleicao='6258')
    state.timeline_official = [
        {'pct_secoes': 10, 'votos': {'13': 100_000, '22': 80_000}, 'restante': 1_000_000},
        {'pct_secoes': 99, 'votos': {'13': 400_000, '22': 300_000}, 'restante': 50_000},
    ]

    class RepaintApp(App):
        def compose(self) -> ComposeResult:
            yield Chart(draw_margin, id='margin')

        def on_mount(self):
            self.query_one(Chart).show(state)

    async def exercise():
        app = RepaintApp()
        async with app.run_test(size=(100, 30), notifications=True) as pilot:
            await pilot.pause()
            chart = app.query_one(Chart)
            first = chart.render().plain
            assert chart.render().plain == first
            for _ in range(3):
                app.notify('Marco da apuração', title='Apuração', timeout=30)
                await pilot.pause()
                await pilot.click('Toast')
                await pilot.pause()
                assert chart.render().plain == first
            await pilot.resize_terminal(80, 24)
            await pilot.pause()
            resized = chart.render().plain
            assert chart.render().plain == resized
            assert state.timeline_official[0]['restante'] == 1_000_000

    asyncio.run(exercise())
