"use client";

import { useRef, useState, type PointerEvent, type ReactNode } from "react";

/* 스와이프로 인정할 최소 이동 거리 (카드 폭의 20%) */
const SWIPE_THRESHOLD_RATIO = 0.2;

interface SwipeCardProps {
  children: ReactNode;
  onSwipeLeft: () => void;
  onSwipeRight: () => void;
}

export default function SwipeCard({
  children,
  onSwipeLeft,
  onSwipeRight,
}: SwipeCardProps) {
  // 드래그 시작 지점의 X 좌표. null 이면 "드래그 중이 아님"
  const startXRef = useRef<number | null>(null);
  // 시작 지점 대비 이동 거리. 카드 위치(transform)와 라벨에 쓰인다
  const [deltaX, setDeltaX] = useState(0);
  // transition 을 켜고 끄는 데만 쓰인다
  const [isDragging, setIsDragging] = useState(false);
  // labelOpacity 계산에만 쓰인다 (라벨 연출 전용)
  const [cardWidth, setCardWidth] = useState(0);

  // pointerUp 과 pointerCancel 두 곳에서 부른다
  const resetDrag = () => {
    startXRef.current = null;
    setDeltaX(0);
    setIsDragging(false);
  };

  const handlePointerDown = (e: PointerEvent<HTMLDivElement>) => {
    startXRef.current = e.clientX;
    setIsDragging(true);
    // transform 영향을 받지 않는 실제 레이아웃 폭
    setCardWidth(e.currentTarget.offsetWidth);
    // 포인터가 카드 밖으로 나가도 이 요소가 계속 이벤트를 받게 한다
    e.currentTarget.setPointerCapture(e.pointerId);
  };

  const handlePointerMove = (e: PointerEvent<HTMLDivElement>) => {
    // 마우스는 누르지 않아도 move 가 계속 발생하므로 걸러낸다
    if (startXRef.current === null) return;
    setDeltaX(e.clientX - startXRef.current);
  };

  const handlePointerUp = (e: PointerEvent<HTMLDivElement>) => {
    if (startXRef.current === null) return;

    const startX = startXRef.current;
    const endX = e.clientX;
    const distance = endX - startX;
    const threshold = e.currentTarget.offsetWidth * SWIPE_THRESHOLD_RATIO;

    // 판정에 쓸 값을 위에서 지역 변수로 잡아뒀으므로 먼저 초기화해도 안전하다
    resetDrag();

    // 기준 미달이면 스와이프가 아니라 단순 클릭/탭으로 본다
    if (Math.abs(distance) < threshold) return;

    if (distance < 0) {
      onSwipeLeft();
    } else {
      onSwipeRight();
    }
  };

  // 판정 기준에 얼마나 가까운지 0~1. 라벨을 점점 진하게 보여주는 데 쓴다
  const labelOpacity = cardWidth
    ? Math.min(Math.abs(deltaX) / (cardWidth * SWIPE_THRESHOLD_RATIO), 1)
    : 0;
  return (
    // 카드가 좌우로 밀려나도 가로 스크롤바가 생기지 않도록 잘라낸다
    <div className="flex w-full justify-center overflow-x-clip">
      <div
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        // 브라우저가 제스처를 가로채면 up 대신 cancel 이 온다
        onPointerCancel={resetDrag}
        style={{
          transform: `translateX(${deltaX}px) rotate(${deltaX * 0.04}deg)`,
          // 끌고 있을 때는 손가락을 즉시 따라가야 하므로 transition 을 끈다
          transition: isDragging ? "none" : "transform 200ms ease-out",
        }}
        // 크기와 배경은 children 이 정한다. 여기는 끌기에 필요한 것만
        className="relative shrink-0 cursor-grab touch-pan-y select-none active:cursor-grabbing"
      >
        {children}

        {/* 끌린 방향에 맞는 라벨만 보여준다 */}
        <span
          className="text-error bg-background/80 absolute top-6 left-6 rounded-md border border-current px-2 py-1 text-sm font-bold"
          style={{ opacity: deltaX < 0 ? labelOpacity : 0 }}
        >
          비선호
        </span>
        <span
          className="text-success bg-background/80 absolute top-6 right-6 rounded-md border border-current px-2 py-1 text-sm font-bold"
          style={{ opacity: deltaX > 0 ? labelOpacity : 0 }}
        >
          선호
        </span>
      </div>
    </div>
  );
}
