import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ArchetypeInfo, PlantKind } from "../types";

/** What was right-clicked on the map (SPEC §8 interaction grammar). Every
 *  target sits at a trench node, so placement actions are always offered. */
export interface MenuTarget {
  kind: "node" | "consumer" | "producer" | "storage";
  id: number | string;
  name: string;
  node: string;
  x: number; // viewport coordinates of the click
  y: number;
  // element context for the remove/config labels
  consumerKind?: "consumer" | "bypass";
  producerKind?: "slack" | "heat_exchanger" | "pump_mass";
}

export type MenuAction =
  | { type: "addHx" }
  | { type: "addPump" }
  | { type: "addStorage" }
  | { type: "addBypass" }
  | { type: "addConsumer"; archetype?: string; qKw?: number }
  | { type: "removeConsumer" }
  | { type: "removeProducer" }
  | { type: "removeStorage" }
  | { type: "storageMode"; mode: "idle" | "charge" | "discharge" }
  | { type: "plantKind"; kind: PlantKind; tColdSource?: "t_amb" | "t_ground" };

/** Context menu on a clicked map element: element-specific actions first
 *  (pin details, remove, storage mode, plant kind), then the placement
 *  items — the node → add producer/storage/bypass/consumer grammar (§8).
 *  Two-page: the consumer archetype picker and the plant-kind picker swap
 *  the page in place. */
export default function ElementMenu({
  target, archetypes, onAction, onPin, onClose,
}: {
  target: MenuTarget;
  archetypes: ArchetypeInfo[];
  onAction: (a: MenuAction) => void;
  onPin: () => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [page, setPage] = useState<"main" | "consumer" | "plant">("main");
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);

  const x = Math.max(4, Math.min(target.x, window.innerWidth - 250));
  const y = Math.max(4, Math.min(target.y, window.innerHeight - 320));
  const item = (key: string, label: string, fn: () => void, close = true) => (
    <button key={key} className="menu-item"
            onClick={() => { fn(); if (close) onClose(); }}>{label}</button>
  );
  const act = (a: MenuAction) => () => onAction(a);

  const placement = [
    <div key="hdr-place" className="menu-hdr">{t("menu.placeHdr")}</div>,
    item("hx", `☀️ ${t("menu.addHx")}`, act({ type: "addHx" })),
    item("pump", `⚙️ ${t("menu.addPump")}`, act({ type: "addPump" })),
    item("stor", `🛢️ ${t("menu.addStorage")}`, act({ type: "addStorage" })),
    item("cons", `🏠 ${t("menu.addConsumer")}…`,
         () => setPage("consumer"), false),
    item("byp", `🔀 ${t("menu.addBypass")}`, act({ type: "addBypass" })),
  ];

  let body: JSX.Element[];
  if (page === "consumer") {
    body = [
      <div key="hdr" className="menu-hdr">{t("menu.consumerHdr")}</div>,
      ...archetypes.map((a) =>
        item(a.id, `🏠 ${a.name}`,
             act({ type: "addConsumer", archetype: a.id }))),
      item("const", `🏠 ${t("menu.constConsumer")}`,
           act({ type: "addConsumer", qKw: 20 })),
      item("back", `← ${t("menu.back")}`, () => setPage("main"), false),
    ];
  } else if (page === "plant") {
    body = [
      <div key="hdr" className="menu-hdr">{t("menu.plantHdr")}</div>,
      item("boiler", `🔥 ${t("menu.plantBoiler")}`,
           act({ type: "plantKind", kind: "boiler" })),
      item("chp", `⚡ ${t("menu.plantChp")}`,
           act({ type: "plantKind", kind: "chp" })),
      item("hpAir", `♨️ ${t("menu.plantHpAir")}`,
           act({ type: "plantKind", kind: "heat_pump", tColdSource: "t_amb" })),
      item("hpGround", `♨️ ${t("menu.plantHpGround")}`,
           act({ type: "plantKind", kind: "heat_pump", tColdSource: "t_ground" })),
      item("back", `← ${t("menu.back")}`, () => setPage("main"), false),
    ];
  } else {
    const specific: JSX.Element[] = [
      item("pin", `📌 ${t("menu.pin")}`, onPin),
    ];
    if (target.kind === "consumer") {
      specific.push(item(
        "rmC",
        `🗑️ ${target.consumerKind === "bypass"
          ? t("menu.removeBypass") : t("menu.removeConsumer")}`,
        act({ type: "removeConsumer" })));
    } else if (target.kind === "producer") {
      if (target.producerKind === "slack") {
        specific.push(item("plant", `🏭 ${t("menu.plantKind")}…`,
                           () => setPage("plant"), false));
      } else {
        specific.push(item("rmP", `🗑️ ${t("menu.removeProducer")}`,
                           act({ type: "removeProducer" })));
      }
    } else if (target.kind === "storage") {
      specific.push(
        item("chg", `⚡ ${t("menu.storageCharge")}`,
             act({ type: "storageMode", mode: "charge" })),
        item("dis", `🔻 ${t("menu.storageDischarge")}`,
             act({ type: "storageMode", mode: "discharge" })),
        item("idle", `⏸ ${t("menu.storageIdle")}`,
             act({ type: "storageMode", mode: "idle" })),
        item("rmS", `🗑️ ${t("menu.removeStorage")}`,
             act({ type: "removeStorage" })),
      );
    }
    body = [...specific, ...placement];
  }

  return (
    <>
      <div className="menu-overlay" onClick={onClose}
           onContextMenu={(e) => { e.preventDefault(); onClose(); }} />
      <div className="el-menu" style={{ left: x, top: y }}>
        <div className="menu-title">{target.name}</div>
        {body}
      </div>
    </>
  );
}
