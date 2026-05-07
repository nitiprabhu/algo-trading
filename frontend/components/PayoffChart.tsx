"use client";

import { useEffect, useRef } from "react";

interface PayoffChartProps {
  spotPrice: number;
  strikes: number[];
  premiums: number[];
  types: ("CE" | "PE")[];
  directions: ("BUY" | "SELL")[];
}

export function PayoffChart({ spotPrice, strikes, premiums, types, directions }: PayoffChartProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const width = canvas.width;
    const height = canvas.height;
    const margin = { top: 40, right: 40, bottom: 40, left: 60 };
    const chartWidth = width - margin.left - margin.right;
    const chartHeight = height - margin.top - margin.bottom;

    // Range: +/- 10% of spot
    const minPrice = spotPrice * 0.9;
    const maxPrice = spotPrice * 1.1;
    const priceRange = maxPrice - minPrice;

    const calculatePnL = (price: number) => {
      let totalPnL = 0;
      for (let i = 0; i < strikes.length; i++) {
        const strike = strikes[i];
        const premium = premiums[i];
        const type = types[i];
        const direction = directions[i];

        let pnl = 0;
        if (type === "CE") {
          pnl = Math.max(0, price - strike) - premium;
        } else {
          pnl = Math.max(0, strike - price) - premium;
        }

        if (direction === "SELL") {
          pnl = -pnl;
        }
        totalPnL += pnl;
      }
      return totalPnL;
    };

    // Find PnL range for scaling
    const steps = 100;
    const pnlValues = [];
    for (let i = 0; i <= steps; i++) {
      const p = minPrice + (i / steps) * priceRange;
      pnlValues.push(calculatePnL(p));
    }
    const maxPnL = Math.max(...pnlValues, 100);
    const minPnL = Math.min(...pnlValues, -100);
    const pnlRange = Math.max(Math.abs(maxPnL), Math.abs(minPnL)) * 1.2;

    const xToPx = (x: number) => margin.left + ((x - minPrice) / priceRange) * chartWidth;
    const yToPx = (y: number) => margin.top + chartHeight / 2 - (y / pnlRange) * (chartHeight / 2);

    // Clear
    ctx.clearRect(0, 0, width, height);

    // Grid
    ctx.strokeStyle = "rgba(255,255,255,0.05)";
    ctx.lineWidth = 1;
    
    // Horizontal center line (Zero PnL)
    ctx.beginPath();
    ctx.moveTo(margin.left, yToPx(0));
    ctx.lineTo(width - margin.right, yToPx(0));
    ctx.stroke();

    // Vertical spot line
    ctx.setLineDash([5, 5]);
    ctx.strokeStyle = "rgba(255,255,255,0.2)";
    ctx.beginPath();
    ctx.moveTo(xToPx(spotPrice), margin.top);
    ctx.lineTo(xToPx(spotPrice), height - margin.bottom);
    ctx.stroke();
    ctx.setLineDash([]);

    // Labels
    ctx.fillStyle = "#91a1aa";
    ctx.font = "10px Inter";
    ctx.textAlign = "center";
    ctx.fillText(`Spot: ${spotPrice.toFixed(0)}`, xToPx(spotPrice), margin.top - 10);

    // Draw Payoff Curve
    ctx.beginPath();
    ctx.lineWidth = 3;
    for (let i = 0; i <= steps; i++) {
      const p = minPrice + (i / steps) * priceRange;
      const v = calculatePnL(p);
      const x = xToPx(p);
      const y = yToPx(v);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }

    // Gradient fill
    const grad = ctx.createLinearGradient(0, margin.top, 0, height - margin.bottom);
    grad.addColorStop(0, "rgba(77, 191, 140, 0.2)");
    grad.addColorStop(0.5, "rgba(0, 0, 0, 0)");
    grad.addColorStop(1, "rgba(239, 111, 108, 0.2)");
    
    const path = new Path2D();
    for (let i = 0; i <= steps; i++) {
      const p = minPrice + (i / steps) * priceRange;
      const v = calculatePnL(p);
      const x = xToPx(p);
      const y = yToPx(v);
      if (i === 0) path.moveTo(x, y);
      else path.lineTo(x, y);
    }
    path.lineTo(xToPx(maxPrice), yToPx(0));
    path.lineTo(xToPx(minPrice), yToPx(0));
    path.closePath();
    ctx.fillStyle = grad;
    ctx.fill(path);

    // Stroke curve
    const strokeGrad = ctx.createLinearGradient(0, margin.top, 0, height - margin.bottom);
    strokeGrad.addColorStop(0, "#4dbf8c");
    strokeGrad.addColorStop(0.5, "#eef4f7");
    strokeGrad.addColorStop(1, "#ef6f6c");
    ctx.strokeStyle = strokeGrad;
    ctx.stroke();

  }, [spotPrice, strikes, premiums, types, directions]);

  return (
    <div className="payoff-surface glass" style={{ padding: "1rem", borderRadius: "12px", overflow: "hidden" }}>
      <canvas 
        ref={canvasRef} 
        width={800} 
        height={400} 
        style={{ width: "100%", height: "100%", display: "block" }} 
      />
    </div>
  );
}
