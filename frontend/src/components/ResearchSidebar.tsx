import { BarChart3, FlaskConical, Lightbulb, Search, Settings2 } from "lucide-react";

export type ResearchView = "research" | "idea" | "experiment" | "results" | "atlas" | "hypotheses" | "experiments";

type Props = {
  activeView: ResearchView;
  onNavigate: (view: ResearchView) => void;
  onSettings: () => void;
  run?: unknown;
  running?: boolean;
};

const items: Array<{ id: ResearchView; label: string; icon: typeof Search }> = [
  { id: "research", label: "研究", icon: Search },
  { id: "idea", label: "Idea", icon: Lightbulb },
  { id: "experiment", label: "实验台", icon: FlaskConical },
  { id: "results", label: "结果展示", icon: BarChart3 },
];

export function ResearchSidebar({ activeView, onNavigate, onSettings }: Props) {
  return <aside className="gew-sidebar" aria-label="研究导航">
    <div className="gew-logo" aria-label="格物 Gewu">
      <span>格物</span>
      <small>GEWU</small>
    </div>
    <nav>
      {items.map(({ id, label, icon: Icon }) => <button
        key={id}
        className={`gew-nav-item ${activeView === id ? "is-active" : ""}`}
        aria-current={activeView === id ? "page" : undefined}
        onClick={() => onNavigate(id)}
      ><Icon size={21} strokeWidth={1.65}/><span>{label}</span></button>)}
    </nav>
    <button className="gew-nav-item gew-settings" onClick={onSettings}><Settings2 size={21} strokeWidth={1.65}/><span>设置</span></button>
  </aside>;
}
