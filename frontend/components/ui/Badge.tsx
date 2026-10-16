export type Tone = "neutral" | "success" | "warning" | "danger" | "accent";

type Props = {
  tone?: Tone;
  solid?: boolean;
  title?: string;
  children: React.ReactNode;
};

export default function Badge({ tone = "neutral", solid = false, title, children }: Props) {
  return (
    <span className={`badge badge-${tone}${solid ? " badge-solid" : ""}`} title={title}>
      {children}
    </span>
  );
}
