/**
 * 食谱卡片组件
 */
import { Recipe } from "@/types/chat";
import { ExternalLink, Clock, ChefHat } from "lucide-react";

interface RecipeCardProps {
  recipe: Recipe;
}

export function RecipeCard({ recipe }: RecipeCardProps) {
  const getScoreColor = (score: number | undefined) => {
    if (score === undefined) return "bg-gray-400";
    if (score >= 4) return "bg-green-500";
    if (score >= 3) return "bg-orange-500";
    return "bg-red-500";
  };

  return (
    <div className="border border-gray-200 rounded-lg p-4 bg-white hover:shadow-md transition-shadow">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1">
          <div className="flex items-center gap-2 mb-2">
            <h3 className="font-semibold text-lg">{recipe.title}</h3>
            {recipe.score !== undefined && (
              <span className={`${getScoreColor(recipe.score)} text-white text-xs px-2 py-1 rounded-full`}>
                {recipe.score}/5
              </span>
            )}
          </div>
          {recipe.reason && (
            <p className="text-gray-600 text-sm mb-2">{recipe.reason}</p>
          )}
          {recipe.difficulty && (
            <div className="flex items-center gap-1 text-sm text-gray-500">
              <ChefHat size={14} />
              <span>{recipe.difficulty}</span>
            </div>
          )}
        </div>
        {recipe.url && (
          <a
            href={recipe.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-blue-500 hover:text-blue-600"
          >
            <ExternalLink size={20} />
          </a>
        )}
      </div>

      {recipe.steps && recipe.steps.length > 0 && (
        <div className="mt-3">
          <h4 className="text-sm font-medium mb-1">制作步骤:</h4>
          <ul className="text-sm text-gray-600 list-disc list-inside space-y-1">
            {recipe.steps.map((step, i) => (
              <li key={i}>{step}</li>
            ))}
          </ul>
        </div>
      )}

      {recipe.seasonings && recipe.seasonings.length > 0 && (
        <div className="mt-3">
          <h4 className="text-sm font-medium mb-1">所需调料:</h4>
          <p className="text-sm text-gray-600">{recipe.seasonings.join(", ")}</p>
        </div>
      )}

      {recipe.cooking_time && (
        <div className="mt-3 flex items-center gap-1 text-sm text-gray-500">
          <Clock size={14} />
          <span>{recipe.cooking_time}</span>
        </div>
      )}
    </div>
  );
}
