# Aleph Timeline Simulator

**Agent Skill production-ready để mô phỏng timeline theo hiệu ứng cánh bướm: counterfactual history, causal graph, human decision nodes, propagation, branch audit, và tích hợp Aleph KB.**

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Release](https://img.shields.io/github/v/release/d-init-d/aleph-timeline-simulator?sort=semver)](https://github.com/d-init-d/aleph-timeline-simulator/releases)

README tiếng Anh: [README.md](README.md)

> Skill này biến một điểm thay đổi thành workflow mô phỏng có kiểm chứng: nghiên cứu baseline, dựng node, chỉ nhận edge có mechanism/evidence, propagate theo thời gian, tạo nhiều nhánh xác suất, và audit uncertainty.

## Dùng khi nào

Dùng skill này khi agent cần:

- mô phỏng alternate history hoặc counterfactual timeline;
- phân tích hiệu ứng cánh bướm từ một điểm thay đổi;
- dựng Aleph-style causal graph với người, sự kiện, yếu tố, bối cảnh, indicator;
- tạo nhiều nhánh tương lai/quá khứ giả định với xác suất;
- dùng D Research để xây evidence ledger cho node/edge/human dossier;
- viết report tách rõ `fact`, `inference`, `simulation`, `counterfactual`.

Không dùng để khẳng định một tương lai chắc chắn, profile người riêng tư, doxxing, stalking, bypass login/paywall/captcha/rate limit, hoặc thu thập dữ liệu cá nhân nhạy cảm.

## Cấu trúc

- `SKILL.md` - lõi Agent Skills portable cho Codex, Claude Code, OpenCode, `.agents`.
- `references/` - hướng dẫn workflow, Aleph integration, D Research, node, edge, propagation, human node, branch, safety, reporting.
- `templates/` - manifest, node, edge, actor dossier, evidence map, branch ledger, propagation trace, validation report.
- `scripts/` - preflight, init workspace, validate, bridge Aleph, score butterfly, render report, install adapters.
- `adapters/` - ghi chú cài đặt cho Codex, Claude Code, OpenCode, generic Agent Skills.

## Cài đặt nhanh

```bash
git clone https://github.com/d-init-d/aleph-timeline-simulator.git
cd aleph-timeline-simulator
npm run self-test
```

Cài vào Codex:

```bash
python scripts/install_adapters.py --target codex --scope user --copy
```

Cài vào Claude Code:

```bash
python scripts/install_adapters.py --target claude-code --scope user --copy
```

Cài vào OpenCode:

```bash
python scripts/install_adapters.py --target opencode --scope user --copy
```

## Quick start

```bash
python scripts/init_simulation_workspace.py --slug oil-shock-2026 --change-point "Oil price rises 40 percent starting June 2026" --out-dir simulations
python scripts/validate_simulation_artifacts.py --workspace simulations/oil-shock-2026 --write-report
python scripts/score_butterfly.py --trace simulations/oil-shock-2026/propagation-trace.jsonl
python scripts/render_simulation_report.py --workspace simulations/oil-shock-2026
```

Prompt mẫu:

```text
Use $aleph-timeline-simulator to simulate an oil price +40% shock starting June 2026. Focus on inflation, central-bank reaction, growth, shipping, and emerging markets over 24 months. Use D Research for evidence and Aleph-style causal branches.
```

## Kiểm tra

```bash
python scripts/validate_skill_package.py .
python scripts/validate_simulation_artifacts.py --examples
python scripts/preflight.py --json
npm run self-test
```

## License

Source-available cho mục đích phi thương mại theo **CC-BY-NC-4.0**. Xem [LICENSE](LICENSE).
