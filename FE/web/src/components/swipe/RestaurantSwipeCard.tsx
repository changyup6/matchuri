import Image from "next/image";

type RestaurantSwipeCardProps = {
  name: string;
  categories: string[];
  tags: string[];
  distanceLabel?: string;
  imageUrl?: string;
};

export default function RestaurantSwipeCard({
  name,
  categories,
  tags,
  distanceLabel,
  imageUrl,
}: RestaurantSwipeCardProps) {
  return (
    <div className="bg-muted relative aspect-560/993 h-[calc(100vh-40px)] overflow-hidden rounded-[1.25rem]">
      {imageUrl && (
        <Image
          src={imageUrl}
          alt={`${name} 대표 사진`}
          fill
          className="object-cover"
          draggable={false}
          priority
        />
      )}

      <div className="from-background via-background/70 absolute inset-x-0 bottom-0 h-1/2 bg-gradient-to-t to-transparent" />

      {/* 정보 영역 */}
      <div className="absolute inset-x-0 bottom-0 px-8 pb-24">
        <h2 className="text-foreground text-[1.75rem] leading-tight font-bold">
          {name}
        </h2>

        <div className="mt-1 flex items-end justify-between">
          <p className="text-muted text-[1.5rem]">{categories.join(" · ")}</p>

          {distanceLabel && (
            <span className="bg-primary rounded-full px-4 py-1 text-[1.25rem] font-bold text-white">
              {distanceLabel}
            </span>
          )}
        </div>

        <ul className="mt-3 flex gap-2">
          {tags.map((tag) => (
            <li
              key={tag}
              className="bg-surface/70 text-foreground rounded-full px-4 py-1 text-[1.25rem] font-medium"
            >
              {tag}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
