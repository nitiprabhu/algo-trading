"use client";

import { createChart } from "lightweight-charts";
import { useEffect, useRef } from "react";

export function EquityChart({ data }: { data: { time: string; equity: number }[] }) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, {
      height: 220,
      layout: { background: { color: "#101418" }, textColor: "#d6dde3" },
      localization: { locale: "en-IN" },
      grid: { vertLines: { color: "#202930" }, horzLines: { color: "#202930" } },
      rightPriceScale: { borderColor: "#2b363f" },
      timeScale: { borderColor: "#2b363f" }
    });
    const series = chart.addAreaSeries({
      lineColor: "#4dbf8c",
      topColor: "rgba(77, 191, 140, 0.35)",
      bottomColor: "rgba(77, 191, 140, 0.02)"
    });
    const orderedData = data
      .map((item) => ({
        time: Math.floor(new Date(item.time).getTime() / 1000) + 19800,
        value: item.equity
      }))
      .sort((left, right) => left.time - right.time)
      .filter((item, index, items) => index === 0 || item.time > items[index - 1].time);

    series.setData(orderedData.map((item) => ({ ...item, time: item.time as never })));
    const resize = () => chart.applyOptions({ width: ref.current?.clientWidth ?? 0 });
    resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.remove();
    };
  }, [data]);

  return <div className="chartSurface" ref={ref} />;
}
