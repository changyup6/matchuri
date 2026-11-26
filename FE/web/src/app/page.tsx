"use client";

import { useState } from "react";
import RestaurantSwipeCard from "@/components/swipe/RestaurantSwipeCard";
import SwipeCard from "@/components/swipe/SwipeCard";

type MockRestaurant = {
  name: string;
  categories: string[];
  tags: string[];
  distanceLabel?: string;
};

// TODO: 백엔드 연동 시 API 응답으로 교체
const MOCK_RESTAURANTS: MockRestaurant[] = [
  {
    name: "소문난성수감자탕",
    categories: ["한식", "탕·찌개"],
    tags: ["해장", "가성비", "노포"],
    distanceLabel: "가까워요",
  },
  {
    name: "역전우동 0410",
    categories: ["일식", "우동"],
    tags: ["혼밥", "빠른식사"],
  },
  {
    name: "청담수하동",
    categories: ["한식", "곰탕"],
    tags: ["점심", "든든한", "조용한"],
    distanceLabel: "가까워요",
  },
  {
    name: "미미면가",
    categories: ["일식", "라멘"],
    tags: ["데이트", "웨이팅", "감성적"],
  },
];

export default function Home() {
  const [index, setIndex] = useState(0);
  const current = MOCK_RESTAURANTS[index];

  const handleSwipeLeft = () => {
    console.log("비선호:", current.name);
    setIndex((prev) => prev + 1);
  };

  const handleSwipeRight = () => {
    console.log("선호:", current.name);
    setIndex((prev) => prev + 1);
  };

  return (
    <div className="flex flex-1 items-center justify-center">
      {current ? (
        <SwipeCard
          key={current.name}
          onSwipeLeft={handleSwipeLeft}
          onSwipeRight={handleSwipeRight}
        >
          <RestaurantSwipeCard
            name={current.name}
            categories={current.categories}
            tags={current.tags}
            distanceLabel={current.distanceLabel}
          />
        </SwipeCard>
      ) : (
        <p className="text-muted text-sm">더 이상 카드가 없습니다.</p>
      )}
    </div>
  );
}
