import type { ReactNode } from "react";

type Props = { title: string; english: string; subtitle: string; actions?: ReactNode; meta?: ReactNode };

export function PageHeader({ title, english, subtitle, actions, meta }: Props) {
  return <header className="gew-page-header">
    <div className="gew-page-heading"><div className="gew-title-line"><h1>{title}</h1><em>{english}</em></div><p>{subtitle}</p>{meta}</div>
    {actions ? <div className="gew-header-actions">{actions}</div> : null}
  </header>;
}
