"use client";

import { useCallback, useEffect, useState } from "react";
import "../history.css";
import { WorkspaceNav } from "../../components/WorkspaceNav";
import { useLanguage } from "../../components/LanguageProvider";

type Metric = { cases: number; citation_accuracy_at_1: number; recall_at_3: number; mrr: number; failed_case_ids: string[] };
type Dataset = "retrieval_v1" | "retrieval_v2";
type Report = { dataset: Dataset; dataset_sha256: string; split: string; offline_test_adapter_only: boolean; real_model_evaluation: boolean; methods: Record<string, Metric> };
const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const methodLabels: Record<string, string> = { bm25: "BM25", deterministic_dense_test_adapter: "确定性测试向量（Mock）", rrf: "RRF 融合" };
const percent = (value: number) => `${(value * 100).toFixed(1)}%`;

export default function EvaluationPage() {
  const { t } = useLanguage();
  const [dataset, setDataset] = useState<Dataset>("retrieval_v1");
  const [split, setSplit] = useState("all");
  const [report, setReport] = useState<Report | null>(null);
  const [notice, setNotice] = useState("");

  const loadReport = useCallback(async (selectedDataset: Dataset, selectedSplit: string) => {
    setNotice("");
    const headers: Record<string, string> = {};
    try {
      const response = await fetch(`${API}/api/v1/evaluation?dataset=${selectedDataset}&split=${selectedSplit}`, { headers, credentials: "include", cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail?.message ?? "无法读取评测报告");
      setReport(payload);
    } catch (error) { setReport(null); setNotice(error instanceof Error ? error.message : "无法读取评测报告"); }
  }, []);

  useEffect(() => {
    void fetch(`${API}/api/v1/auth/me`, { credentials: "include", cache: "no-store" }).then(async (response) => {
      if (!response.ok) throw new Error("请先登录后查看评测报告");
      await loadReport("retrieval_v1", "all");
      }).catch((error) => setNotice(error instanceof Error ? error.message : "登录状态无效"));
  }, [loadReport]);

  function downloadReport() {
    if (!report) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }));
    const link = document.createElement("a"); link.href = url; link.download = `dealguard-evaluation-${report.dataset}-${report.split}.json`; link.click(); URL.revokeObjectURL(url);
  }

  return <><WorkspaceNav /><main className="dg-history"><div className="dg-history-main">
    <header className="dg-history-head"><div><h1>{t("检索评测", "Retrieval evaluation")}</h1><p>{t("查看固定语料上的政策引用指标，并导出可复核 JSON 报告。", "Review policy citation metrics on fixed datasets and export a reproducible JSON report.")}</p></div></header>
    <section className="dg-history-panel" style={{ marginTop: 12 }}>
      <div className="dg-history-filters"><label>{t("评测语料", "Evaluation dataset")}<select value={dataset} onChange={(event) => { const next = event.target.value as Dataset; setDataset(next); setSplit("all"); setReport(null); void loadReport(next, "all"); }}><option value="retrieval_v1">v1: 50 {t("条政策检索（英）", "English policy queries")}</option><option value="retrieval_v2">v2: 20 {t("条政策检索（英中）", "bilingual policy queries")}</option></select></label><label>{t("数据切分", "Data split")}<select value={split} onChange={(event) => { setSplit(event.target.value); void loadReport(dataset, event.target.value); }}><option value="all">{t("全部", "All")} ({dataset === "retrieval_v1" ? 50 : 20})</option><option value="development">{t("开发集", "Development")} ({dataset === "retrieval_v1" ? 35 : 10})</option><option value="holdout">{t("留出集", "Holdout")} ({dataset === "retrieval_v1" ? 15 : 10})</option></select></label><button className="dg-history-button" onClick={downloadReport} disabled={!report}>{t("导出 JSON", "Export JSON")}</button></div>
      {notice && <p className="dg-history-notice" role="status">{notice}</p>}
      {report && <>
        <p className="dg-help">数据 SHA-256：<code>{report.dataset_sha256}</code></p>
        <p className="dg-help">{t("模式：", "Mode: ")}{report.offline_test_adapter_only ? t("离线测试适配器", "Offline test adapter") : t("未知", "Unknown")} · {t("真实模型评测：", "Real model evaluation: ")}{report.real_model_evaluation ? t("是", "Yes") : t("否", "No")}. {t("测试向量结果不代表真实 Embedding 质量。", "Test-vector results do not represent real embedding quality.")}</p>
        <div className="dg-history-table-wrap"><table className="dg-history-table"><thead><tr><th>方法</th><th>样本数</th><th>Citation Accuracy@1</th><th>Recall@3</th><th>MRR</th><th>Top-1 失败案例</th></tr></thead><tbody>
          {Object.entries(report.methods).map(([name, metric]) => <tr key={name}><td>{methodLabels[name] ?? name}</td><td>{metric.cases}</td><td>{percent(metric.citation_accuracy_at_1)}</td><td>{percent(metric.recall_at_3)}</td><td>{percent(metric.mrr)}</td><td>{metric.failed_case_ids.length ? metric.failed_case_ids.join(", ") : "无"}</td></tr>)}
        </tbody></table></div>
      </>}
    </section>
  </div></main></>;
}
