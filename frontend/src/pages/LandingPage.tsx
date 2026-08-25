import { ArrowDown, ArrowRight, FileCheck2, FlaskConical, Lightbulb, Search } from "lucide-react";

type Props = {
  onEnter: () => void;
};

const journey = [
  {
    title: "研究问题",
    english: "Research",
    copy: "明确问题边界、研究目标与可验证路径。",
    icon: Search,
  },
  {
    title: "Idea 假设",
    english: "Evidence & Ideas",
    copy: "检索真实文献，以证据形成候选假设。",
    icon: Lightbulb,
  },
  {
    title: "实验验证",
    english: "Experiment",
    copy: "运行真实实验，追踪主指标与科学限制。",
    icon: FlaskConical,
  },
  {
    title: "结论沉淀",
    english: "Results",
    copy: "汇总结论边界、复现信息与研究产物。",
    icon: FileCheck2,
  },
];

export function LandingPage({ onEnter }: Props) {
  const showMethod = () => document.getElementById("research-method")?.scrollIntoView({ behavior: "smooth", block: "start" });

  return <main className="gew-landing">
    <header className="landing-nav" aria-label="格物首页导航">
      <button className="landing-brand" onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })} aria-label="返回格物首页顶部">
        <img src="/gewu-logo-transparent.png" alt="" />
        <span><b>格物</b><small>GEWU</small></span>
      </button>
      <div className="landing-nav-actions">
        <button className="landing-text-action" onClick={showMethod}>研究方法</button>
        <button className="landing-enter compact" onClick={onEnter}>进入研究 <ArrowRight size={18}/></button>
      </div>
    </header>

    <section className="landing-hero" aria-labelledby="landing-title">
      <div className="landing-hero-copy">
        <h1 id="landing-title">格物</h1>
        <span className="landing-gewu">GEWU</span>
        <p className="landing-intro">从问题、证据与假设出发，走向可验证、可复现的研究结论。</p>
        <div className="landing-hero-actions">
          <button className="landing-enter" onClick={onEnter}>进入研究 <ArrowRight size={21}/></button>
          <button className="landing-method" onClick={showMethod}>查看研究方法 <ArrowDown size={18}/></button>
        </div>
      </div>
      <div className="landing-hero-mark" aria-hidden="true">
        <img src="/gewu-hero-linework.png" alt="" />
      </div>
    </section>

    <section className="landing-journey" aria-label="研究流程">
      <div className="landing-journey-grid">
        {journey.map(({ title, english, copy, icon: Icon }, index) => <article key={title}>
          <span className="journey-index">0{index + 1}</span>
          <Icon size={30} strokeWidth={1.45}/>
          <h3>{title}</h3>
          <em>{english}</em>
          <p>{copy}</p>
        </article>)}
      </div>
    </section>

    <section className="landing-method-detail" id="research-method" aria-labelledby="method-title">
      <div>
        <p>RESEARCH METHOD</p>
        <h2 id="method-title">每个结论，都能回到证据与实验</h2>
      </div>
      <div className="landing-method-principles">
        <article><span>01</span><h3>可追溯</h3><p>研究问题、文献依据、候选假设与结果产物保持同一条证据链。</p></article>
        <article><span>02</span><h3>可验证</h3><p>Idea 必须进入实验，主指标、运行过程和失败记录都有真实来源。</p></article>
        <article><span>03</span><h3>可复现</h3><p>结论同时保留适用边界、配置参数与产物信息，支持再次运行。</p></article>
      </div>
    </section>
  </main>;
}
