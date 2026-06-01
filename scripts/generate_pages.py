import argparse
import math
import shutil
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from html import escape
from pathlib import Path

from beancount import loader
from beancount.core.data import Transaction


EXPENSE_PREFIX = 'Expenses:AWS:'
CURRENCY = 'USD'


@dataclass(frozen=True)
class TransactionExpense:
    date: date
    narration: str
    document: str
    billing_period: str
    total: Decimal
    services: list[str]


@dataclass(frozen=True)
class DashboardData:
    title: str
    total: Decimal
    latest_month: str
    latest_month_total: Decimal
    latest_transaction_date: date | None
    month_totals: list[tuple[str, Decimal]]
    account_totals: list[tuple[str, Decimal]]
    transactions: list[TransactionExpense]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Generate a static dashboard for the Beancount ledger.')
    parser.add_argument('--input', default='dabr-ca.bean', type=Path, help='Beancount input file.')
    parser.add_argument('--output', default='public', type=Path, help='Output directory for GitHub Pages.')
    return parser.parse_args()


def money(amount: Decimal) -> str:
    value = amount.quantize(Decimal('0.01'))
    sign = '-' if value < 0 else ''
    return f'{sign}${abs(value):,.2f}'


def account_label(account: str) -> str:
    return account.removeprefix(EXPENSE_PREFIX).replace(':', ' / ')


def metadata_text(transaction: Transaction, key: str) -> str:
    value = transaction.meta.get(key)
    return '' if value is None else str(value)


def billing_period(transaction: Transaction) -> str:
    start = metadata_text(transaction, 'billing_period_start')
    end = metadata_text(transaction, 'billing_period_end')
    if start and end:
        return f'{start} to {end}'
    return start or end


def posting_location(meta: dict[str, object] | None) -> str:
    if meta is None:
        return 'unknown location'
    return f'{meta.get("filename")}:{meta.get("lineno")}'


def load_dashboard_data(input_path: Path) -> DashboardData:
    entries, errors, options = loader.load_file(str(input_path))
    if errors:
        joined_errors = '\n'.join(str(error) for error in errors)
        raise SystemExit(f'Could not load {input_path}:\n{joined_errors}')

    title = str(options.get('title') or 'Ledger')
    month_totals: dict[str, Decimal] = defaultdict(Decimal)
    account_totals: dict[str, Decimal] = defaultdict(Decimal)
    transactions: list[TransactionExpense] = []

    for entry in entries:
        if not isinstance(entry, Transaction):
            continue

        transaction_total = Decimal('0')
        transaction_accounts: dict[str, Decimal] = defaultdict(Decimal)
        for posting in entry.postings:
            if not posting.account.startswith(EXPENSE_PREFIX):
                continue

            units = posting.units
            if units is None:
                raise SystemExit(f'Missing amount for {posting.account} in {posting_location(posting.meta)}.')
            if units.currency != CURRENCY:
                location = posting_location(posting.meta)
                raise SystemExit(f'Unexpected currency {units.currency} in {location}; expected {CURRENCY}.')

            amount = units.number
            if amount is None:
                raise SystemExit(f'Missing numeric amount for {posting.account} in {posting_location(posting.meta)}.')
            transaction_total += amount
            transaction_accounts[posting.account] += amount
            account_totals[posting.account] += amount
            month_totals[entry.date.strftime('%Y-%m')] += amount

        if transaction_total:
            services = [
                f'{account_label(account)} ({money(amount)})'
                for account, amount in sorted(transaction_accounts.items())
            ]
            transactions.append(
                TransactionExpense(
                    date=entry.date,
                    narration=entry.narration or '',
                    document=metadata_text(entry, 'document'),
                    billing_period=billing_period(entry),
                    total=transaction_total,
                    services=services,
                )
            )

    sorted_month_totals = sorted(month_totals.items())
    sorted_account_totals = sorted(account_totals.items(), key=lambda item: item[1], reverse=True)
    latest_month, latest_month_total = sorted_month_totals[-1] if sorted_month_totals else ('', Decimal('0'))
    latest_transaction_date = max((transaction.date for transaction in transactions), default=None)

    return DashboardData(
        title=title,
        total=sum(month_totals.values(), Decimal('0')),
        latest_month=latest_month,
        latest_month_total=latest_month_total,
        latest_transaction_date=latest_transaction_date,
        month_totals=sorted_month_totals,
        account_totals=sorted_account_totals,
        transactions=sorted(transactions, key=lambda transaction: transaction.date, reverse=True),
    )


def table_rows(rows: Iterable[str]) -> str:
    return '\n'.join(rows)


def build_monthly_chart(month_totals: list[tuple[str, Decimal]]) -> str:
    if not month_totals:
        return '<p>No expenses found.</p>'

    width = 960
    height = 320
    top = 24
    right = 20
    bottom = 54
    left = 74
    plot_width = width - left - right
    plot_height = height - top - bottom
    max_value = max(total for _, total in month_totals)
    if max_value <= 0:
        max_value = Decimal('1')

    step = plot_width / len(month_totals)
    label_interval = max(1, math.ceil(len(month_totals) / 12))
    bars: list[str] = []

    for index, (month, total) in enumerate(month_totals):
        bar_height = float(total / max_value) * plot_height
        x = left + index * step + step * 0.15
        y = top + plot_height - bar_height
        bar_width = max(2, step * 0.7)
        bars.append(
            '<rect class="chart-bar" '
            f'x="{x:.2f}" y="{y:.2f}" width="{bar_width:.2f}" height="{bar_height:.2f}">'
            f'<title>{escape(month)}: {escape(money(total))}</title>'
            '</rect>'
        )
        if index % label_interval == 0 or index == len(month_totals) - 1:
            label_x = x + bar_width / 2
            bars.append(
                f'<text class="chart-label" x="{label_x:.2f}" y="{height - 18}" '
                f'text-anchor="middle">{escape(month)}</text>'
            )

    y_axis = [
        f'<line class="chart-axis" x1="{left}" y1="{top + plot_height}" x2="{width - right}" y2="{top + plot_height}" />',
        f'<line class="chart-axis" x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_height}" />',
        f'<text class="chart-label" x="{left - 10}" y="{top + 4}" text-anchor="end">{escape(money(max_value))}</text>',
        f'<text class="chart-label" x="{left - 10}" y="{top + plot_height}" text-anchor="end">$0</text>',
    ]

    return (
        '<svg class="chart" viewBox="0 0 960 320" role="img" aria-labelledby="monthly-chart-title">'
        '<title id="monthly-chart-title">Monthly AWS expenses</title>'
        f'{table_rows(y_axis)}'
        f'{table_rows(bars)}'
        '</svg>'
    )


def render_index(data: DashboardData, source_name: str) -> str:
    generated_at = datetime.now(tz=UTC).strftime('%Y-%m-%d %H:%M UTC')
    latest_date = data.latest_transaction_date.isoformat() if data.latest_transaction_date else 'n/a'

    account_rows = table_rows(
        '<tr>'
        f'<td>{escape(account_label(account))}</td>'
        f'<td class="numeric">{escape(money(total))}</td>'
        '</tr>'
        for account, total in data.account_totals
    )
    monthly_rows = table_rows(
        '<tr>'
        f'<td>{escape(month)}</td>'
        f'<td class="numeric">{escape(money(total))}</td>'
        '</tr>'
        for month, total in reversed(data.month_totals)
    )

    return f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(data.title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f8fa;
      --panel: #ffffff;
      --text: #172033;
      --muted: #5f6b7a;
      --border: #d9dee7;
      --accent: #2563eb;
      --accent-strong: #1746a2;
      --soft: #e9f0ff;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 15px/1.5 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    header {{
      border-bottom: 1px solid var(--border);
      background: var(--panel);
    }}
    main, .header-inner {{
      width: min(1120px, calc(100% - 32px));
      margin: 0 auto;
    }}
    .header-inner {{
      padding: 28px 0 24px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 32px;
      line-height: 1.15;
      letter-spacing: 0;
    }}
    h2 {{
      margin: 0 0 14px;
      font-size: 20px;
      letter-spacing: 0;
    }}
    p {{
      margin: 0;
      color: var(--muted);
    }}
    a {{
      color: var(--accent-strong);
      font-weight: 600;
    }}
    main {{
      padding: 24px 0 48px;
    }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 24px;
    }}
    .metric {{
      min-width: 0;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--panel);
      padding: 16px;
    }}
    .metric-label {{
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 8px;
    }}
    .metric-value {{
      font-size: 24px;
      font-weight: 700;
      line-height: 1.2;
      overflow-wrap: anywhere;
    }}
    section {{
      margin-top: 24px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--panel);
      padding: 18px;
    }}
    .chart-wrap {{
      overflow-x: auto;
    }}
    .chart {{
      display: block;
      min-width: 720px;
      width: 100%;
      height: auto;
    }}
    .chart-bar {{
      fill: var(--accent);
    }}
    .chart-axis {{
      stroke: #9ca8ba;
      stroke-width: 1;
    }}
    .chart-label {{
      fill: var(--muted);
      font-size: 12px;
    }}
    .table-wrap {{
      overflow-x: auto;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 560px;
    }}
    th, td {{
      padding: 10px 12px;
      border-bottom: 1px solid var(--border);
      text-align: left;
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-size: 13px;
      font-weight: 700;
      background: #fbfcfe;
    }}
    tbody tr:hover {{
      background: var(--soft);
    }}
    .numeric {{
      text-align: right;
      white-space: nowrap;
      font-variant-numeric: tabular-nums;
    }}
    .actions {{
      margin-top: 12px;
    }}
    @media (max-width: 860px) {{
      .summary {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
    }}
    @media (max-width: 560px) {{
      main, .header-inner {{
        width: min(100% - 20px, 1120px);
      }}
      h1 {{
        font-size: 26px;
      }}
      .summary {{
        grid-template-columns: 1fr;
      }}
      section {{
        padding: 14px;
      }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="header-inner">
      <h1>{escape(data.title)}</h1>
      <p>Static dashboard generated from <a href="{escape(source_name)}">{escape(source_name)}</a> on {escape(generated_at)}.</p>
    </div>
  </header>
  <main>
    <div class="summary" aria-label="Expense summary">
      <div class="metric">
        <div class="metric-label">Total expenses</div>
        <div class="metric-value">{escape(money(data.total))}</div>
      </div>
      <div class="metric">
        <div class="metric-label">Latest month</div>
        <div class="metric-value">{escape(data.latest_month or 'n/a')}</div>
      </div>
      <div class="metric">
        <div class="metric-label">Latest month total</div>
        <div class="metric-value">{escape(money(data.latest_month_total))}</div>
      </div>
      <div class="metric">
        <div class="metric-label">Latest transaction</div>
        <div class="metric-value">{escape(latest_date)}</div>
      </div>
    </div>

    <section>
      <h2>Monthly Expenses</h2>
      <div class="chart-wrap">
        {build_monthly_chart(data.month_totals)}
      </div>
    </section>

    <section>
      <h2>Expense Breakdown</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>Account</th><th class="numeric">Total</th></tr>
          </thead>
          <tbody>
            {account_rows}
          </tbody>
        </table>
      </div>
    </section>

    <section>
      <h2>Monthly Totals</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>Month</th><th class="numeric">Total</th></tr>
          </thead>
          <tbody>
            {monthly_rows}
          </tbody>
        </table>
      </div>
    </section>
  </main>
</body>
</html>
'''


def prepare_output_dir(output_path: Path, input_path: Path) -> None:
    resolved_output = output_path.resolve()
    resolved_input = input_path.resolve()
    if resolved_output == resolved_input or resolved_output == resolved_input.parent:
        raise SystemExit(f'Refusing to write generated site to {output_path}.')
    if output_path.exists() and not output_path.is_dir():
        raise SystemExit(f'Output path {output_path} exists and is not a directory.')
    if output_path.exists():
        shutil.rmtree(output_path)
    output_path.mkdir(parents=True)


def main() -> None:
    args = parse_args()
    input_path = args.input
    output_path = args.output
    data = load_dashboard_data(input_path)

    prepare_output_dir(output_path, input_path)
    source_name = input_path.name
    shutil.copyfile(input_path, output_path / source_name)
    (output_path / 'index.html').write_text(render_index(data, source_name), encoding='utf-8')


if __name__ == '__main__':
    main()
