from pathlib import Path
import json,time
R=Path('/path/to/workspace/多智能体系统实验数据/fcb-dow-research/B_cross_engine')
def read(p):return json.loads(p.read_text(encoding='utf-8'))
meta=read(R/'run_meta.json');meta.update(stage='B-formal-complete',completed_unix=time.time(),B1_B3_runs=300,B1_B3_requests=3210,statistical_preregistration='config/preregistration.json',isolation='per-run flush + extra_key, token-exact replay, no prefix salt tokens',formal_audit=read(R/'evidence/formal-audit.json'),hypothesis_conclusions=read(R/'reports/conclusions.json'))
meta['engine_launches']=[read(p) for p in sorted((R/'evidence').glob('*-page*-launch.json'))]
meta['toolchain_versions']=read(R/'evidence/toolchain-versions.json')
if (R/'evidence/ancillary-state.json').exists():meta['ancillary']=read(R/'evidence/ancillary-state.json')
meta['configuration_note']='Triton attention backend; both CUDA graphs off; BF16 activations/KV; Qwen BF16 / Llama ModelOpt FP8; TP1 single H100. Actual model-specific memory budget and page-size are in engine_launches.'
(R/'run_meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
report=R/'B_stage_gate_report.md';text=report.read_text(encoding='utf-8')
notice='> 本文件为正式实验前的阶段闸记录。用户已于 2026-10-06 批准继续，正式 B1–B3 已完成；最终结论见 `B_results_summary.md`。原阶段状态保留为历史证据。\n\n'
if not text.startswith('> 本文件为'):report.write_text(notice+text,encoding='utf-8')
print('Final run metadata updated')
