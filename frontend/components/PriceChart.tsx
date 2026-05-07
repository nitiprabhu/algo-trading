"use client";

import { createChart, ColorType } from "lightweight-charts";
import { useEffect, useRef } from "react";

interface PriceChartProps {
  data: { time: string; price: number }[];
  symbol: string;
  color?: string;
}

export function PriceChart({ data, symbol, color = "#2962FF" }: PriceChartProps) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!ref.current) return;

    const chart = createChart(ref.current, {
      height: 200,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#91a1aa",
      },
      localization: {
        locale: "en-IN",
      },
      grid: {
        vertLines: { color: "rgba(32, 41, 48, 0.3)" },
        horzLines: { color: "rgba(32, 41, 48, 0.3)" },
      },
      rightPriceScale: {
        borderColor: "rgba(38, 50, 58, 0.5)",
        scaleMargins: {
            top: 0.1,
            bottom: 0.1,
        },
      },
      timeScale: {
        borderColor: "rgba(38, 50, 58, 0.5)",
        timeVisible: true,
        secondsVisible: false,
      },
      handleScroll: {
          mouseWheel: true,
          pressedMouseMove: true,
      },
      handleScale: {
          axisPressedMouseMove: true,
          mouseWheel: true,
          pinch: true,
      },
    });

    const series = chart.addAreaSeries({
      lineColor: color,
      topColor: `${color}44`,
      bottomColor: `${color}00`,
      lineWidth: 2,
      crosshairMarkerVisible: true,
    });

    const orderedData = data
      .filter((item) => item.price > 0)
      .map((item) => ({
        time: Math.floor(new Date(item.time).getTime() / 1000) + 19800,
        value: item.price
      }))
      .sort((left, right) => left.time - right.time)
      .filter((item, index, items) => index === 0 || item.time > items[index - 1].time);

    if (orderedData.length > 0) {
        series.setData(orderedData as any);
        chart.timeScale().fitContent();
    }

    const resize = () => {
      if (ref.current) {
        chart.applyOptions({ width: ref.current.clientWidth });
      }
    };
    
    resize();
    window.addEventListener("resize", resize);

    return () => {
      window.removeEventListener("resize", resize);
      chart.remove();
    };
  }, [data, color]);

  return (
    <div className="price-chart-container" style={{ position: "relative", width: "100%" }}>
      <div 
        ref={ref} 
        style={{ width: "100%" }}
      />
      <div style={{
        position: "absolute",
        top: "10px",
        left: "10px",
        zIndex: 1,
        pointerEvents: "none",
        fontSize: "0.8rem",
        fontWeight: "bold",
        color: color,
        textShadow: "0 0 10px rgba(0,0,0,0.5)"
      }}>
        {symbol}
      </div>
    </div>
  );
}
